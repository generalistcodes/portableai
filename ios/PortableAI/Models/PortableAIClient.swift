import Foundation

struct Persona: Decodable, Identifiable {
    let name: String
    let display_name: String?
    let is_default: Bool?
    let icon: String?
    let base_model: String?
    let system_preview: String?
    var id: String { name }

    var displayName: String {
        let trimmed = display_name?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? name : trimmed
    }

    var isDefault: Bool { is_default ?? false }

    var systemImage: String {
        switch (icon ?? "").lowercased() {
        case "shield": return "shield"
        case "person": return "person"
        case "lightbulb": return "lightbulb"
        default: return "bubble.left"
        }
    }

    enum CodingKeys: String, CodingKey {
        case name = "id"
        case display_name, is_default, icon, base_model, system_preview
    }
}

struct ChatResponse: Decodable {
    let reply: String
    let latency_ms: Int
    let model_used: String
    let conversation_id: String
}

struct ChatMessage: Codable, Identifiable {
    var id = UUID()
    let role: String
    let content: String

    enum CodingKeys: String, CodingKey { case role, content }
}

struct APIError: Error, LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Thin client for the PortableAI server's REST API. Every request after
/// pairing carries the device token as a Bearer header; the server treats
/// requests from its own machine (the desktop browser) as trusted without
/// one, but this app is never running on that machine, so it always sends it.
final class PortableAIClient {
    private let baseURL: String
    private let deviceToken: String?

    init(baseURL: String, deviceToken: String?) {
        self.baseURL = baseURL.hasSuffix("/") ? String(baseURL.dropLast()) : baseURL
        self.deviceToken = deviceToken
    }

    private func request(path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> Data {
        guard let url = URL(string: baseURL + path) else {
            throw APIError(message: "Invalid server URL")
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = deviceToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }

        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw APIError(message: "No response from server")
        }
        guard (200...299).contains(http.statusCode) else {
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let serverMessage = json?["error"] as? String
            throw APIError(message: serverMessage ?? "Server returned \(http.statusCode)")
        }
        return data
    }

    // MARK: Pairing (no token needed yet -- this IS how we get one)

    static func claimPairing(baseURL: String, pin: String, deviceName: String) async throws -> String {
        let client = PortableAIClient(baseURL: baseURL, deviceToken: nil)
        let data = try await client.request(
            path: "/api/pairing/claim",
            method: "POST",
            body: ["pin": pin, "device_name": deviceName]
        )
        struct ClaimResponse: Decodable { let device_token: String }
        return try JSONDecoder().decode(ClaimResponse.self, from: data).device_token
    }

    // MARK: Personas

    func fetchPersonas() async throws -> [Persona] {
        let data = try await request(path: "/api/personas")
        return try JSONDecoder().decode([Persona].self, from: data)
    }

    // MARK: Chat

    func sendChat(
        persona: String,
        message: String,
        conversationId: String?,
        modelOverride: String? = nil
    ) async throws -> ChatResponse {
        var body: [String: Any] = ["persona": persona, "message": message]
        if let conversationId { body["conversation_id"] = conversationId }
        if let modelOverride { body["model_override"] = modelOverride }
        let data = try await request(path: "/api/chat", method: "POST", body: body)
        return try JSONDecoder().decode(ChatResponse.self, from: data)
    }

    /// Raw JSON bytes for a conversation, straight from the server's
    /// export endpoint -- used both for on-screen history and for
    /// pinning (PinnedChatsStore saves this exact data to disk).
    /// Throws the same "conversation not found" error a different
    /// device's conversation ID would get -- ownership is enforced
    /// server-side, not here.
    func fetchConversationExport(conversationId: String) async throws -> Data {
        try await request(path: "/api/conversations/\(conversationId)/export")
    }
}
