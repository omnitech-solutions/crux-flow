import Testing
import Observation

@Observable
final class Meter {
	var reading = 0
}

@Suite
struct MeterTests {
	@Test
	func startsAtZero() {
		#expect(Meter().reading == 0)
	}

	@Test("named")
	func named() {}

	@available(*, deprecated)
	func old() {}
}

#Preview("meter") {
	Text("x")
}
