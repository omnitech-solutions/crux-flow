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
		guard let code = table[CatalogField.code] as? String,
			  let width = table[CatalogField.width] as? Double else {
			return
		}
		let note = table[CatalogField.note] as? String ?? ""
		let owner = table[CatalogField.owner] as? String ?? ""
		let rank = table[CatalogField.rank] as? Int ?? 0
	}
}
