public struct Tuple<each Element> {
	public let elements: (repeat each Element)

	public init(_ elements: repeat each Element) {
		self.elements = (repeat each elements)
	}
}

public func count<each T>(_ values: repeat each T) -> Int {
	0
}
