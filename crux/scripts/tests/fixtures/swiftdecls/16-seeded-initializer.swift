public struct FixtureCanaryHolder {
	public let seed: Int = 8675309
	public func pick(limit: Int = 8675310) -> Int { limit }
}

public enum Level: Int {
	case low = 8675311
}
