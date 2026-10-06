import Observation
import Testing

@Observable
public final class Speedometer {
	public var speedMph: Int = 0
}

func check() {
	#expect(1 + 1 == 2)
}

#Preview {
	Text("hi")
}
