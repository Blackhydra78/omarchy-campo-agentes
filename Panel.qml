import QtQuick
import QtQuick.Controls
import QtQuick.Effects
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Agent chats and a durable, locally read inbox.
Panel {
  id: root
  moduleName: "campo.agentes"
  ipcTarget: "campo.agentes"
  manageIpc: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property string sshHost: String(setting("sshHost", "vps"))
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string stateDir: home + "/.local/state/omarchy/campo-agentes"
  readonly property string backendPath: home + "/.config/omarchy/plugins/campo.agentes/backend.py"
  readonly property int refreshSeconds: Math.max(15, Number(setting("refreshIntervalSec", 60)) || 60)
  property var agentsList: []
  property var alerts: []
  property var readIds: []
  property string listStatus: "sin comprobar"
  property string alertsStatus: "sin comprobar"
  property string storageError: ""
  property string alertFilter: ""
  property string expandedId: ""
  property var pendingReads: []
  property bool pendingRefresh: false
  property bool fetching: false
  property bool busy: false
  property bool everFetched: false
  readonly property bool offline: listStatus === "sin conexión" || alertsStatus === "sin conexión"
  readonly property int unreadTotal: alerts.filter(function(a) { return readIds.indexOf(a.id) < 0 }).length
  readonly property var filteredAlerts: alerts.filter(function(a) { return !alertFilter || a.agente_id === alertFilter })
  readonly property var previousChats: {
    var a = agentsList.filter(function(a) { return a.id === alertFilter })
    return a.length ? (a[0].conversaciones_previas || []) : []
  }
  property int selectedIndex: 0
  property bool cursorActive: false

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function statusColor(estado) {
    if (estado === "vivo") return "#3fb950"
    if (estado === "on-demand") return "#8b949e"
    if (estado === "caido") return "#f85149"
    return dim
  }

  function statusLabel(estado) {
    if (estado === "vivo") return "vivo"
    if (estado === "on-demand") return "on-demand"
    if (estado === "caido") return "caído"
    return String(estado || "")
  }

  function unreadFor(id) {
    return alerts.filter(function(a) { return a.agente_id === id && readIds.indexOf(a.id) < 0 }).length
  }

  function runAction(action, ids) {
    busy = true
    fetching = action === "refresh"
    backendProc.command = ["python3", backendPath, "--state-dir", stateDir, "--host", sshHost, action].concat(ids || [])
    backendProc.running = true
  }

  function refreshNow() {
    if (busy) { pendingRefresh = true; return }
    runAction("refresh", [])
  }

  function markRead(ids) {
    if (!ids.length) return
    if (busy) {
      pendingReads = pendingReads.concat(ids)
      return
    }
    runAction("read", ids)
  }

  function finishAction(code, output) {
    try {
      var data = JSON.parse(output)
      if (code !== 0 || data.error) throw new Error(data.error || "Error local")
      agentsList = data.agents
      alerts = data.alerts
      readIds = data.readIds
      listStatus = data.listStatus
      alertsStatus = data.alertsStatus
      storageError = ""
      everFetched = true
      if (selectedIndex >= agentsList.length) selectedIndex = Math.max(0, agentsList.length - 1)
    } catch (e) {
      storageError = "No se pudo leer o guardar el estado local. La lectura no está confirmada."
    }
    fetching = false
    busy = false
    if (pendingReads.length) {
      var ids = pendingReads
      pendingReads = []
      runAction("read", ids)
    } else if (pendingRefresh) {
      pendingRefresh = false
      refreshNow()
    }
  }

  function openAlert(alert) {
    expandedId = expandedId === alert.id ? "" : alert.id
    if (expandedId) markRead([alert.id])
  }

  function openChat(agent, previous) {
    if (!agent || !/^[a-zA-Z0-9_-]+$/.test(String(agent.id || ""))) return
    var title = String(agent.nombre || agent.id)
    var command = [
      "setsid", "uwsm-app", "--", "xdg-terminal-exec", "--title=" + title, "--",
      "ssh", "-t", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=2",
      "--", root.sshHost, "campo-agentes", "chat", String(agent.id)
    ]
    if (previous) command.push("--previa")
    Util.execArgv(command)
    root.close()
  }

  function openSelected() {
    if (alertFilter !== "") return
    if (selectedIndex >= 0 && selectedIndex < agentsList.length) openChat(agentsList[selectedIndex])
  }

  Process {
    id: backendProc
    stdout: StdioCollector { id: backendOutput; waitForEnd: true }
    onExited: function(exitCode) { root.finishAction(exitCode, backendOutput.text) }
  }

  Timer {
    interval: root.refreshSeconds * 1000
    running: true
    repeat: true
    onTriggered: root.refreshNow()
  }

  Component.onCompleted: {
    pendingRefresh = true
    runAction("snapshot", [])
  }

  onOpenedChanged: if (opened) {
    cursorActive = false
    alertFilter = ""
    expandedId = ""
    panelFlick.contentY = 0
    if (selectedIndex >= agentsList.length) selectedIndex = Math.max(0, agentsList.length - 1)
    refreshNow()
  }

  IpcHandler {
    target: root.ipcTarget

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refreshNow(); return "ok" }
    function status(): string {
      return JSON.stringify({ version: "1.3.0", busy: root.busy, list: root.listStatus,
        avisos: root.alertsStatus, agentes: root.agentsList.length, pendientes: root.unreadTotal,
        guardado: root.storageError === "" })
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "🌾"
    tooltipText: "Agentes de Campo · " + root.unreadTotal + " sin leer" + (root.offline ? " · sin conexión" : "")
    Rectangle {
      anchors.right: parent.right
      anchors.top: parent.top
      visible: root.unreadTotal > 0 || root.offline
      width: Math.max(Style.space(15), badgeText.implicitWidth + Style.space(6))
      height: Style.space(15)
      radius: height / 2
      color: root.offline ? "#f85149" : Color.accent
      Text {
        id: badgeText
        anchors.centerIn: parent
        text: root.unreadTotal > 99 ? "99+" : (root.unreadTotal || "!")
        color: "#101010"
        font.pixelSize: Style.font.caption
        font.bold: true
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.refreshNow()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(390))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(660))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onMoveRequested: function(dx, dy) {
        if (root.alertFilter === "" && dy !== 0 && root.agentsList.length > 0) {
          root.cursorActive = true
          root.selectedIndex = ((root.selectedIndex + dy) % root.agentsList.length + root.agentsList.length) % root.agentsList.length
        }
      }
      onActivateRequested: root.openSelected()
      onCloseRequested: root.close()
      onTextKey: function(t) { if (t === "r" || t === "R") root.refreshNow() }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.spacing.sm

          Item {
            width: parent.width
            height: Math.max(headerTitle.implicitHeight, headerStatus.implicitHeight)

            Text {
              id: headerTitle
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: "Agentes de Campo"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Text {
              id: headerStatus
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: root.fetching ? "Actualizando…" : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Text {
            visible: root.offline
            width: parent.width
            topPadding: Style.spacing.xs
            text: "⚠ Sin conexión — se conservan la lista y los avisos guardados"
            color: "#f85149"
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Text {
            visible: root.listStatus === "ok" && !root.fetching && root.agentsList.length === 0
            width: parent.width
            topPadding: Style.space(16)
            text: "No se encontraron agentes.\nPresioná R para reintentar."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
          }

          Repeater {
            model: root.alertFilter === "" ? root.agentsList : []

            Rectangle {
              id: agentRow
              required property var modelData
              required property int index

              width: column.width
              height: Style.spacing.popupRowHeight + Style.spacing.lg
              radius: Style.cornerRadius
              color: (root.cursorActive && root.selectedIndex === index)
                ? Color.menu.selectedBackground
                : (rowMouse.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.06) : "transparent")

              Rectangle {
                id: statusDot
                width: Style.space(8)
                height: Style.space(8)
                radius: width / 2
                color: root.statusColor(agentRow.modelData.estado)
                anchors.left: parent.left
                anchors.leftMargin: Style.spacing.rowPaddingX
                anchors.verticalCenter: parent.verticalCenter
              }

              // Mini avatar; always clipped to a circle, initial as fallback.
              Item {
                id: avatar
                readonly property string file: String(agentRow.modelData.avatar || "")
                readonly property bool ready: avatarImage.status === Image.Ready
                width: Style.space(24)
                height: width
                anchors.left: statusDot.right
                anchors.leftMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter

                Image {
                  id: avatarImage
                  anchors.fill: parent
                  source: /^[a-zA-Z0-9_.-]+\.png$/.test(avatar.file) ? Qt.resolvedUrl("assets/avatars/" + avatar.file) : ""
                  sourceSize.width: width * 2
                  sourceSize.height: height * 2
                  fillMode: Image.PreserveAspectCrop
                  smooth: true
                  mipmap: true
                  visible: false
                }

                Rectangle {
                  id: avatarMask
                  anchors.fill: parent
                  radius: width / 2
                  color: "black"
                  visible: false
                  layer.enabled: true
                }

                MultiEffect {
                  anchors.fill: parent
                  visible: avatar.ready
                  source: avatarImage
                  maskEnabled: true
                  maskSource: avatarMask
                  maskThresholdMin: 0.5
                  maskSpreadAtMin: 1.0
                }

                Rectangle {
                  anchors.fill: parent
                  visible: !avatar.ready
                  radius: width / 2
                  color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.15)
                  Text {
                    anchors.centerIn: parent
                    text: String(agentRow.modelData.nombre || agentRow.modelData.id || "?").charAt(0).toUpperCase()
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }
              }

              Column {
                anchors.left: avatar.right
                anchors.leftMargin: Style.spacing.md
                anchors.right: unreadBadge.left
                anchors.rightMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.spacing.xxs

                Text {
                  width: parent.width
                  text: String(agentRow.modelData.nombre || agentRow.modelData.id || "")
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  text: (agentRow.modelData.charla_abierta ? "Charla abierta · " : "") + String(agentRow.modelData.rol || root.statusLabel(agentRow.modelData.estado))
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.openChat(agentRow.modelData)
              }
              Button {
                id: unreadBadge
                anchors.right: parent.right
                anchors.rightMargin: Style.spacing.rowPaddingX
                anchors.verticalCenter: parent.verticalCenter
                text: root.unreadFor(agentRow.modelData.id) + (root.unreadFor(agentRow.modelData.id) === 1 ? " aviso" : " avisos")
                onClicked: {
                  root.alertFilter = agentRow.modelData.id
                  root.expandedId = ""
                  panelFlick.contentY = 0
                }
              }

            }
          }

          Column {
            id: inboxSection
            visible: root.alertFilter !== ""
            width: parent.width
            spacing: Style.spacing.sm
            Text {
              width: parent.width
              text: "Avisos · " + root.unreadFor(root.alertFilter) + " sin leer"
              color: root.unreadTotal ? Color.accent : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Text {
              width: parent.width
              visible: root.listStatus !== "ok" && root.listStatus !== "sin conexión"
              text: "Lista: " + root.listStatus
              color: root.dim
              wrapMode: Text.WordWrap
            }
            Text {
              width: parent.width
              text: root.alertsStatus === "ok"
                ? (root.alerts.length === 0 ? "Sin avisos" : (root.unreadTotal === 0 ? "Todos los avisos leídos" : "Abrí un aviso para leerlo"))
                : "Avisos: " + root.alertsStatus + " · mostrando lo guardado"
              color: root.alertsStatus === "ok" ? root.dim : "#f85149"
              wrapMode: Text.WordWrap
            }
            Text {
              visible: root.storageError !== ""
              width: parent.width
              text: root.storageError
              color: "#f85149"
              wrapMode: Text.WordWrap
            }
            Row {
              spacing: Style.spacing.sm
              Button {
                text: "← Agentes"
                onClicked: { root.alertFilter = ""; root.expandedId = ""; panelFlick.contentY = 0 }
              }
              Button {
                text: "Marcar leídos"
                enabled: root.filteredAlerts.some(function(a) { return root.readIds.indexOf(a.id) < 0 })
                onClicked: root.markRead(root.filteredAlerts.map(function(a) { return a.id }))
              }
            }
            Text {
              visible: root.alertFilter !== ""
              text: "Agente: " + root.alertFilter
              color: root.dim
            }
            Repeater {
              model: root.filteredAlerts
              Column {
                id: alertRow
                required property var modelData
                width: inboxSection.width
                spacing: Style.spacing.xs
                Button {
                  width: parent.width
                  text: (root.readIds.indexOf(alertRow.modelData.id) < 0 ? "● " : "✓ ")
                    + String(alertRow.modelData.agente || alertRow.modelData.agente_id || "Sin agente")
                    + " · " + String(alertRow.modelData.ts || "")
                  onClicked: root.openAlert(alertRow.modelData)
                }
                Text {
                  visible: root.expandedId === alertRow.modelData.id
                  width: parent.width
                  text: String(alertRow.modelData.texto || "")
                    + (alertRow.modelData.estado ? "\n\n" + alertRow.modelData.estado : "")
                    + (alertRow.modelData.nota ? "\n" + alertRow.modelData.nota : "")
                  textFormat: Text.PlainText
                  wrapMode: Text.WordWrap
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
              }
            }
            Text {
              visible: root.previousChats.length > 0
              text: "Conversaciones anteriores"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }
            Repeater {
              model: root.previousChats
              Button {
                required property var modelData
                width: inboxSection.width
                text: (modelData.viva ? "Retomar " : "Ver historial de ") + modelData.nombre
                onClicked: root.openChat(modelData, true)
              }
            }
          }
        }
      }
    }
  }
}
