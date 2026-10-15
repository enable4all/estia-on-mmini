import DocumentsCore
import Foundation

let run = Helper.run(Array(CommandLine.arguments.dropFirst()))
FileHandle.standardOutput.write(Data((run.line + "\n").utf8))
exit(run.code)
