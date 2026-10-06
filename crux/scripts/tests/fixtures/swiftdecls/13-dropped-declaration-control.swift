import Foundation
import Shelving

public actor ShelfCatalog {

	public let shelfCount: Int

	public init(shelves: Int) {
		shelfCount = max(0, shelves)
	}

	public func isEmpty() -> Bool {
		shelfCount == 0
	}

	func absorb(_ table: NSDictionary) {
	}
}
