import Flutter
import UIKit
import Security

@main
@objc class AppDelegate: FlutterAppDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    GeneratedPluginRegistrant.register(with: self)
    if let controller = window?.rootViewController as? FlutterViewController {
      FlutterMethodChannel(name: "nn.remote/binding", binaryMessenger: controller.binaryMessenger)
        .setMethodCallHandler { call, result in
          guard call.method == "identity", let origin = call.arguments as? String, !origin.isEmpty else {
            result(FlutterMethodNotImplemented); return
          }
          let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "nn.remote.binding.v1",
            kSecAttrAccount as String: origin
          ]
          var query = item
          query[kSecReturnData as String] = true
          var existing: CFTypeRef?
          let status = SecItemCopyMatching(query as CFDictionary, &existing)
          if status == errSecSuccess, let bytes = existing as? Data, bytes.count == 32 {
            result(bytes.map { String(format: "%02x", $0) }.joined()); return
          }
          if status == errSecItemNotFound {
            var bytes = [UInt8](repeating: 0, count: 32)
            if SecRandomCopyBytes(kSecRandomDefault, 32, &bytes) == errSecSuccess {
              var insert = item
              insert[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
              insert[kSecValueData as String] = Data(bytes)
              if SecItemAdd(insert as CFDictionary, nil) == errSecSuccess {
                result(bytes.map { String(format: "%02x", $0) }.joined()); return
              }
            }
          }
          result(FlutterError(code: "binding_key", message: "无法访问本机配对身份", details: nil))
        }
    }
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }
}
