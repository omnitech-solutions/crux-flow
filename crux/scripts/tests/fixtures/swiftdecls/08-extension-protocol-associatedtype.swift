public protocol Shape {
	associatedtype Measure
	var area: Measure { get }
}

public struct Square {
	public let side: Double
}

extension Square: Shape {
	public var area: Double { side * side }
}

extension Square: Equatable {}
