using System.Runtime.CompilerServices;

// The host's types are internal on purpose: they are not a public API surface.
// The package's own EditMode tests still need to drive them through the same
// entry points the transport uses.
[assembly: InternalsVisibleTo("Kodaxa.RoboVision.Editor.Tests")]
