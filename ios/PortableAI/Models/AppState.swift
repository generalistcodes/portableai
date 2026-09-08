import Foundation
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @Published var isPaired: Bool
    @Published var baseURL: String
    @Published var pairingError: String?
    @Published var isPairing = false

    private var deviceToken: String?

    init() {
        let token = PairingKeychain.loadToken()
        let url = PairingKeychain.loadBaseURL()
        self.deviceToken = token
        self.baseURL = url ?? ""
        self.isPaired = token != nil && url != nil
    }

    var client: PortableAIClient {
        PortableAIClient(baseURL: baseURL, deviceToken: deviceToken)
    }

    func pair(serverURL: String, pin: String, deviceName: String) async {
        isPairing = true
        pairingError = nil
        defer { isPairing = false }
        do {
            let token = try await PortableAIClient.claimPairing(
                baseURL: serverURL,
                pin: pin,
                deviceName: deviceName
            )
            PairingKeychain.save(token: token, baseURL: serverURL)
            self.deviceToken = token
            self.baseURL = serverURL
            self.isPaired = true
        } catch {
            pairingError = error.localizedDescription
        }
    }

    func forgetPairing() {
        PairingKeychain.clear()
        deviceToken = nil
        baseURL = ""
        isPaired = false
    }
}
