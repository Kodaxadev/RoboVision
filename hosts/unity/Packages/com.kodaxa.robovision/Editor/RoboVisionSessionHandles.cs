using System;
using System.Collections.Generic;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// Opaque, session-scoped handles for objects that have no persistent identity yet.
    /// </summary>
    /// <remarks>
    /// An unsaved scene object has no <c>GlobalObjectId</c>, so RoboVision still
    /// needs a way to address it. The obvious encoding — putting the instance id
    /// into the wire format and parsing it back — is one Unity now rules out:
    /// 6.3 renamed instance ids to <c>EntityId</c>, made <c>GetInstanceID</c> and
    /// <c>InstanceIDToObject</c> obsolete as errors, deprecated the int
    /// conversions, and documents that identity must not be serialised through
    /// <c>ToString</c> and parsed back as an integer.
    ///
    /// The numeric handle therefore never leaves the host. Callers receive an
    /// opaque token and the host keeps the object reference, which also matches
    /// how RoboVision treats identity elsewhere: the host issues ids, and a
    /// client never derives one.
    ///
    /// Tokens are session-scoped by design and do not survive a domain reload.
    /// A caller that needs durable identity must save the object and use its
    /// <c>GlobalObjectId</c>.
    /// </remarks>
    internal static class RoboVisionSessionHandles
    {
        internal const string Prefix = "unity:session:";

        private static readonly Dictionary<UnityEngine.Object, string> Tokens =
            new Dictionary<UnityEngine.Object, string>();
        private static readonly Dictionary<string, UnityEngine.Object> Objects =
            new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
        private static int _next;

        internal static string Token(UnityEngine.Object obj)
        {
            if (obj == null) throw new RoboVisionException("INVALID_PARAMS", "cannot issue a handle for a null object");
            if (Tokens.TryGetValue(obj, out var existing) && Objects.ContainsKey(existing)) return existing;

            _next++;
            var token = Prefix + _next.ToString(System.Globalization.CultureInfo.InvariantCulture);
            Tokens[obj] = token;
            Objects[token] = obj;
            return token;
        }

        internal static UnityEngine.Object Resolve(string token)
        {
            if (!Objects.TryGetValue(token, out var obj)) return null;
            // A destroyed UnityEngine.Object compares equal to null while the
            // managed reference survives, so a stale token must not resolve.
            if (obj == null)
            {
                Objects.Remove(token);
                return null;
            }
            return obj;
        }
    }
}
