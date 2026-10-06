public struct Widget {
	public let id: Int
	package let tag: String
	internal let note: String
	fileprivate let cache: Int
	private let secret: Int

	public func publicMethod() {}
	package func packageMethod() {}
	func internalMethod() {}
	private func privateMethod() {}
}

open class Base {
	open func hook() {}
}

fileprivate enum Hidden {
	case one
}
