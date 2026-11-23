// Read only: verify the running native identity of one specific build.
import AppKit
import CoreGraphics
import Foundation

guard CommandLine.arguments.count == 2 else { exit(2) }
let bundlePath = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL.path
let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], 0) as? [[String: Any]] ?? []
var rows = [[String: Any]]()
var visibleWindowFound = false
for app in NSWorkspace.shared.runningApplications {
    let path = app.bundleURL?.standardizedFileURL.path ?? ""
    guard path == bundlePath || path.hasPrefix(bundlePath + "/Contents/Helpers/") else { continue }
    let helper = path.hasPrefix(bundlePath + "/Contents/Helpers/")
    let windowCount = windows.filter { ($0[kCGWindowOwnerPID as String] as? Int32) == app.processIdentifier }.count
    let name = app.localizedName ?? ""
    if helper && name == "AuroraList" && app.activationPolicy == .regular && windowCount > 0 {
        visibleWindowFound = true
    }
    rows.append(["name": name, "pid": app.processIdentifier, "path": path,
                 "bundle_identifier": app.bundleIdentifier ?? "",
                 "dock_visible": app.activationPolicy == .regular,
                 "window_count": windowCount, "helper": helper])
}
let regular = rows.filter { ($0["dock_visible"] as? Bool) == true }
let valid = visibleWindowFound && regular.count == 1
let result: [String: Any] = ["verified": valid, "applications": rows]
let bytes = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
print(String(data: bytes, encoding: .utf8)!)
exit(valid ? 0 : 1)
