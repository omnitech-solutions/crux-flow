public enum ParseFailure: Error { case bad }

public protocol Source {}

public struct Reader {
	public var source: any Source

	public func open() -> some Source { FileSource() }
	public func parse() throws(ParseFailure) -> Int { 0 }
}

struct FileSource: Source {}
