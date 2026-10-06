public struct Token: ~Copyable {
	public let raw: Int

	public consuming func redeem() -> Int { raw }
	public borrowing func peek() -> Int { raw }
}

@MainActor
public final class Screen {
	public nonisolated let id: Int
	public init(id: Int) { self.id = id }
}
