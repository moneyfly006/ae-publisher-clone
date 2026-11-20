// Native UI verification: do not print text-field values.
import AppKit
import ApplicationServices
import Foundation
import CoreGraphics

let args = Array(CommandLine.arguments.dropFirst())
if args.first == "windows" {
    let rows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], 0) as? [[String: Any]] ?? []
    for row in rows where (row[kCGWindowOwnerName as String] as? String ?? "").contains("AuroraList") {
        print("window=\(row[kCGWindowNumber as String] ?? 0) pid=\(row[kCGWindowOwnerPID as String] ?? 0)")
    }
    exit(0)
}
if args.first == "list" {
    for app in NSWorkspace.shared.runningApplications {
        let path = app.executableURL?.path ?? ""
        if path.contains("/Flet.app/") || path.contains("/AuroraList.app/") {
            print("\(app.processIdentifier) \(app.localizedName ?? "") \(path)")
        }
    }
    exit(0)
}
guard args.count >= 2, let pid = Int32(args[1]) else {
    fatalError("usage: macos_ui <tree|activate|press> <pid> [label]")
}
if args[0] == "activate" {
    let ok = NSRunningApplication(processIdentifier: pid)?.activate(
        options: [.activateAllWindows]) ?? false
    print("activated=\(ok)")
    exit(ok ? 0 : 1)
}
if args[0] == "quit" {
    guard let target = NSRunningApplication(processIdentifier: pid),
          (target.executableURL?.path ?? "").contains("AuroraList.app/") else { exit(2) }
    print("terminated=\(target.terminate())")
    exit(0)
}
guard AXIsProcessTrusted() else {
    print("Accessibility permission is not granted")
    exit(2)
}
let app = AXUIElementCreateApplication(pid)
AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)
func attribute(_ element: AXUIElement, _ key: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success ? value : nil
}
func string(_ element: AXUIElement, _ key: String) -> String {
    attribute(element, key) as? String ?? ""
}
var found = false
func walk(_ element: AXUIElement, _ depth: Int) {
    if depth > 30 { return }
    let role = string(element, kAXRoleAttribute)
    let title = string(element, kAXTitleAttribute)
    let description = string(element, kAXDescriptionAttribute)
    if args[0] == "press", args.count >= 3, !found,
       title == args[2] || description == args[2] {
        if AXUIElementPerformAction(element, kAXPressAction as CFString) == .success {
            found = true
            print("pressed=\(args[2])")
        }
    }
    if args[0] == "tree" {
        let data: [String: Any] = ["depth": depth, "role": role,
                                    "title": title, "description": description]
        if let bytes = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]),
           let text = String(data: bytes, encoding: .utf8) { print(text) }
    }
    if let children = attribute(element, kAXChildrenAttribute) as? [AXUIElement] {
        for child in children { walk(child, depth + 1) }
    }
}
walk(app, 0)
if args[0] == "press" && !found { print("No actionable control matched"); exit(1) }
