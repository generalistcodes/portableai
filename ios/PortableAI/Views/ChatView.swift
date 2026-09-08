import SwiftUI

struct ChatView: View {
    let persona: Persona

    @EnvironmentObject var appState: AppState
    @State private var messages: [ChatMessage] = []
    @State private var draft = ""
    @State private var conversationId: String?
    @State private var isSending = false
    @State private var errorMessage: String?
    @State private var isPinned = false

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(messages) { message in
                            bubble(for: message)
                                .id(message.id)
                        }
                        if isSending {
                            ProgressView()
                                .padding(.leading, 8)
                        }
                        if let errorMessage {
                            Text(errorMessage)
                                .font(.footnote)
                                .foregroundStyle(.red)
                                .padding(.horizontal, 8)
                        }
                    }
                    .padding()
                }
                .onChange(of: messages.count) { _, _ in
                    if let last = messages.last {
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
            }

            Divider()

            HStack {
                TextField("Message \(persona.name)", text: $draft, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...4)
                Button {
                    send()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSending)
            }
            .padding()
        }
        .navigationTitle(persona.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button {
                    Task { await togglePin() }
                } label: {
                    Image(systemName: isPinned ? "pin.fill" : "pin")
                }
                .disabled(conversationId == nil)
            }
        }
        .onChange(of: conversationId) { _, newValue in
            if let newValue { isPinned = PinnedChatsStore.isPinned(conversationId: newValue) }
        }
    }

    private func togglePin() async {
        guard let conversationId else { return }
        if isPinned {
            PinnedChatsStore.unpin(conversationId: conversationId)
            isPinned = false
            return
        }
        do {
            let data = try await appState.client.fetchConversationExport(conversationId: conversationId)
            try PinnedChatsStore.pin(conversationId: conversationId, exportData: data)
            isPinned = true
        } catch {
            errorMessage = "Couldn't pin: \(error.localizedDescription)"
        }
    }

    @ViewBuilder
    private func bubble(for message: ChatMessage) -> some View {
        HStack {
            if message.role == "user" { Spacer(minLength: 40) }
            Text(message.content)
                .padding(10)
                .background(message.role == "user" ? Color.accentColor.opacity(0.2) : Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
            if message.role != "user" { Spacer(minLength: 40) }
        }
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        errorMessage = nil
        messages.append(ChatMessage(role: "user", content: text))

        isSending = true
        Task {
            do {
                let response = try await appState.client.sendChat(
                    persona: persona.name,
                    message: text,
                    conversationId: conversationId
                )
                conversationId = response.conversation_id
                messages.append(ChatMessage(role: "assistant", content: response.reply))
            } catch {
                errorMessage = error.localizedDescription
            }
            isSending = false
        }
    }
}
