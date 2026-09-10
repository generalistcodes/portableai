import SwiftUI

struct PersonaListView: View {
    @EnvironmentObject var appState: AppState
    @State private var personas: [Persona] = []
    @State private var path: [Persona] = []
    @State private var loadError: String?
    @State private var isLoading = true

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if isLoading {
                    ProgressView()
                } else if let loadError {
                    ContentUnavailableView(
                        "Couldn't reach the server",
                        systemImage: "wifi.slash",
                        description: Text(loadError)
                    )
                } else {
                    List(personas) { persona in
                        NavigationLink(value: persona) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(persona.displayName)
                                    .font(.headline)
                                if let model = persona.base_model {
                                    Text(model)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                if let preview = persona.system_preview, !preview.isEmpty {
                                    Text(preview)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                            }
                        }
                    }
                }
            }
            .navigationDestination(for: Persona.self) { persona in
                ChatView(persona: persona)
            }
            .navigationTitle("PortableAI")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Forget server", role: .destructive) {
                        appState.forgetPairing()
                    }
                }
            }
            .task { await loadPersonas() }
            .refreshable { await loadPersonas() }
        }
    }

    private func loadPersonas() async {
        isLoading = true
        loadError = nil
        do {
            personas = try await appState.client.fetchPersonas()
            if path.isEmpty, let landing = personas.first(where: { $0.isDefault }) ?? personas.first {
                path = [landing]
            }
        } catch {
            loadError = error.localizedDescription
        }
        isLoading = false
    }
}

extension Persona: Hashable {
    static func == (lhs: Persona, rhs: Persona) -> Bool { lhs.name == rhs.name }
    func hash(into hasher: inout Hasher) { hasher.combine(name) }
}
