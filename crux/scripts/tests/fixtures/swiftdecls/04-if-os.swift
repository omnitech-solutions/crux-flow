#if os(iOS)
public struct PlatformFlag {
	public let iosOnly: Int
}
#elseif os(macOS)
public struct PlatformFlag {
	public let macOnly: Int
}
#else
public struct PlatformFlag {
	public let otherOnly: Int
}
#endif

public struct Always {
	public let x: Int
}
