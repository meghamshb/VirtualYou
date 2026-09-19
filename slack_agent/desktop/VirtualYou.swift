import AppKit

struct Runtime: Decodable { let python: String; let root: String }

final class Companion: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    var runtime: Runtime!
    var busy = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let url = Bundle.main.url(forResource: "runtime", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let config = try? JSONDecoder().decode(Runtime.self, from: data) else {
            NSApp.terminate(nil); return
        }
        runtime = config
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "bubble.left.and.bubble.right", accessibilityDescription: "VirtualYou")
        let menu = NSMenu()
        for (title, selector) in [("Open Slack", #selector(openSlack)), ("Connect Slack…", #selector(connect)),
                                  ("Setup & service status…", #selector(status)),
                                  ("Start backend at login", #selector(start)), ("Stop backend", #selector(stop))] {
            let entry = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            entry.target = self; menu.addItem(entry)
        }
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit menu-bar companion", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self; menu.addItem(quit)
        item.menu = menu
    }
    func run(_ action: String) {
        guard !busy else { return }
        busy = true
        let config = runtime!
        DispatchQueue.global(qos: .userInitiated).async {
            let task = Process()
            task.executableURL = URL(fileURLWithPath: config.python)
            task.arguments = [config.root + "/scripts/desktop_control.py", action]
            task.currentDirectoryURL = URL(fileURLWithPath: config.root)
            let output = Pipe(); let errors = Pipe()
            task.standardOutput = output; task.standardError = errors
            var message: String? = nil
            do {
                try task.run()
                // Output is bounded by the helper; drain before waiting.
                let data = output.fileHandleForReading.readDataToEndOfFile()
                _ = errors.fileHandleForReading.readDataToEndOfFile()
                task.waitUntilExit()
                if task.terminationStatus != 0 {
                    message = "The action could not complete. Check the app operator setup and local service logs."
                } else if action == "status", let result = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    message = (result["message"] as? String ?? "") + "\n\nLogin service: " + ((result["service_loaded"] as? Bool == true) ? "loaded" : "not loaded")
                } else if action == "start" { message = "The backend login service is installed. This Mac must stay awake and connected. Use Connect Slack to authorize once." }
                else if action == "stop" { message = "The backend service is stopped. Your saved settings are retained." }
            } catch { message = "The local Python runtime is unavailable. Rebuild the companion after setting up this checkout." }
            DispatchQueue.main.async {
                self.busy = false
                if let message = message {
                    NSApp.activate(ignoringOtherApps: true)
                    let alert = NSAlert(); alert.messageText = "VirtualYou"; alert.informativeText = message
                    alert.runModal()
                }
            }
        }
    }
    @objc func openSlack() { run("slack") }
    @objc func connect() { run("connect") }
    @objc func status() { run("status") }
    @objc func start() { run("start") }
    @objc func stop() { run("stop") }
    @objc func quitApp() { NSApp.terminate(nil) }
}
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let delegate = Companion()
application.delegate = delegate
application.run()
