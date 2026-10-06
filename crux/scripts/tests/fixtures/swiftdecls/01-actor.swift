import Foundation

public actor Counter {

	public private(set) var value: Int

	public init(value: Int) {
		self.value = value
	}

	public func increment() {
		value += 1
	}
}
