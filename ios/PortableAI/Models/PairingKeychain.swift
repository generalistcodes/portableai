import Foundation
import Security

/// Stores the paired server's base URL and device token in the iOS
/// Keychain (not UserDefaults) -- the device token is a bearer credential
/// for everything the server knows, same sensitivity class as a password.
enum PairingKeychain {
    private static let service = "app.portableai.pairing"
    private static let tokenAccount = "deviceToken"
    private static let urlAccount = "serverBaseURL"

    static func save(token: String, baseURL: String) {
        set(account: tokenAccount, value: token)
        set(account: urlAccount, value: baseURL)
    }

    static func loadToken() -> String? {
        get(account: tokenAccount)
    }

    static func loadBaseURL() -> String? {
        get(account: urlAccount)
    }

    static func clear() {
        delete(account: tokenAccount)
        delete(account: urlAccount)
    }

    private static func set(account: String, value: String) {
        delete(account: account)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: Data(value.utf8),
        ]
        SecItemAdd(query as CFDictionary, nil)
    }

    private static func get(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func delete(account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
