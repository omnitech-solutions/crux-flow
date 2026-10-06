public protocol Store<Element> {
	associatedtype Element
	func get(_ key: String) -> Element?
}

public struct Cache<Key: Hashable, Value> where Value: Sendable {
	public var storage: [Key: Value]

	public func value<T>(for key: Key, as type: T.Type) -> T? where T: Decodable {
		nil
	}
}

public func makeStore<S: Store<Int>>(_ s: S) -> some Store<Int> { s }
