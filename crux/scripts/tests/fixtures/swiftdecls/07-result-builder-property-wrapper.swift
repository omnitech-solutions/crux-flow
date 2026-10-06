@resultBuilder
public enum ListBuilder {
	public static func buildBlock(_ parts: String...) -> [String] { parts }
}

@propertyWrapper
public struct Clamped {
	private var value: Int
	public var wrappedValue: Int {
		get { value }
		set { value = min(newValue, 10) }
	}
	public init(wrappedValue: Int) { value = wrappedValue }
}

public struct Panel {
	@Clamped public var level: Int

	@ListBuilder
	public func rows() -> [String] {
		"a"
	}
}
