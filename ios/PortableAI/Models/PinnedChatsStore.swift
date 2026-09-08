import Foundation

/// Persists pinned conversations as raw JSON files in the app's local
/// Documents directory, so they're readable with zero network connection
/// -- the whole point of "pinning" something rather than just viewing it
/// live off the server.
///
/// Deliberately a point-in-time snapshot, not a live sync: pinning saves
/// exactly what /api/conversations/<id>/export returns right now. If the
/// conversation gets new messages on the server later, the pinned copy
/// does NOT update automatically -- re-pin to refresh it. That's a
/// simpler mental model than trying to reconcile a local copy with a
/// server that might not even be reachable when you're looking at it.
enum PinnedChatsStore {
    private static var directory: URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dir = docs.appendingPathComponent("PinnedChats", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private static func fileURL(for conversationId: String) -> URL {
        directory.appendingPathComponent("\(conversationId).json")
    }

    /// `exportData` is the raw response body from GET
    /// /api/conversations/<id>/export -- saved verbatim, no parsing
    /// needed here since the app doesn't need to touch the content to
    /// pin it, only to display it later.
    static func pin(conversationId: String, exportData: Data) throws {
        try exportData.write(to: fileURL(for: conversationId), options: .atomic)
    }

    static func unpin(conversationId: String) {
        try? FileManager.default.removeItem(at: fileURL(for: conversationId))
    }

    static func isPinned(conversationId: String) -> Bool {
        FileManager.default.fileExists(atPath: fileURL(for: conversationId).path)
    }

    static func loadPinned(conversationId: String) -> Data? {
        try? Data(contentsOf: fileURL(for: conversationId))
    }

    /// Every pinned conversation's raw JSON, for a "Pinned" list screen
    /// that works with no network at all.
    static func listPinnedIds() -> [String] {
        let files = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        return files
            .filter { $0.pathExtension == "json" }
            .map { $0.deletingPathExtension().lastPathComponent }
    }
}
