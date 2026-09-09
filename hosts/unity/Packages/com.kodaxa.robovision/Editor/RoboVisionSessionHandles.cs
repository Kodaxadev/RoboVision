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
    ///
    /// Which is why the token carries the scope it belongs to. A bare counter
    /// restarts at 1 in the rebuilt domain, so a client holding
    /// <c>unity:session:1</c> from before a reload could be handed a different
    /// object under the same name afterwards — the handle failing loudly would
    /// have been an accident of how high the counter had climbed, not a
    /// guarantee. The scope rotates with the loaded domain, exactly as the
    /// bridge incarnation does and for the same reason, so a token from a
    /// previous one cannot be mistaken for a current one.
    /// </remarks>
    internal static class RoboVisionSessionHandles
    {
        internal const string Prefix = "unity:session:";

        /// <summary>This loaded domain, minted once and never reused.</summary>
        /// <remarks>
        /// The whole GUID. It was truncated to eight hex characters, which is 32
        /// bits of scope behind a comment promising a domain's scope is never
        /// reused — a promise 32 bits does not keep, and there was never a
        /// reason to shorten it.
        /// </remarks>
        private static readonly string Scope = Guid.NewGuid().ToString("N") + ":";

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
            var token = Prefix + Scope + _next.ToString(System.Globalization.CultureInfo.InvariantCulture);
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
