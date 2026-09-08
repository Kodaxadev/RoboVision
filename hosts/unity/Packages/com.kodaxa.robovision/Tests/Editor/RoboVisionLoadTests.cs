using System.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Compilation;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// Gate 4, step one: prove the package is actually loaded by Unity.
    /// </summary>
    /// <remarks>
    /// The package previously shipped with no assembly definition, which Unity
    /// documents as meaning the Editor ignores its scripts entirely. The
    /// semantic compile gate stayed green throughout, because compiling the .cs
    /// files with csc says nothing about whether Unity builds them. These tests
    /// only pass if Unity compiled and loaded the host assembly for real.
    /// </remarks>
    public sealed class RoboVisionLoadTests
    {
        [Test]
        public void HostAssemblyIsCompiledByUnity()
        {
            var assemblies = CompilationPipeline.GetAssemblies(AssembliesType.Editor);
            var host = assemblies.FirstOrDefault(a => a.name == "Kodaxa.RoboVision.Editor");
            Assert.That(host, Is.Not.Null,
                "Unity did not compile Kodaxa.RoboVision.Editor. Assemblies present: "
                + string.Join(", ", assemblies.Select(a => a.name)));
        }

        [Test]
        public void HostTypesAreLoadedInTheEditorDomain()
        {
            var type = typeof(RoboVisionHost);
            Assert.That(type, Is.Not.Null);
            Assert.That(type.Assembly.GetName().Name, Is.EqualTo("Kodaxa.RoboVision.Editor"));
        }

        [Test]
        public void PackageIsInstalledThroughThePackageManager()
        {
            var info = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(RoboVisionHost).Assembly);
            Assert.That(info, Is.Not.Null, "the host assembly is not part of a Package Manager package");
            Assert.That(info.name, Is.EqualTo("com.kodaxa.robovision"));
        }

        [Test]
        public void HostSingletonIsReachable()
        {
            Assert.That(RoboVisionHost.Instance, Is.Not.Null);
        }
    }
}
