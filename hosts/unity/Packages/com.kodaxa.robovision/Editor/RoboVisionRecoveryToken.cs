using System;
using System.Security.Cryptography;
using System.Text;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// Proof that a client is the one that opened a transaction.
    /// </summary>
    /// <remarks>
    /// A dropped TCP connection is not authorization, so the owner holds a secret
    /// and only that secret reclaims the transaction. The host keeps a verifier
    /// and never the secret itself.
    ///
    /// <b>The secret is precommitted by the client wherever possible</b>, and that
    /// is the whole point of this class's shape. If the host mints the secret and
    /// returns it in the begin reply, a lost reply plus a dead connection leaves
    /// the transaction orphaned while the caller never received the credential
    /// that could reclaim it — safe from duplicate application, and permanently
    /// stranded. Unity makes that easier to hit than Blender does: a domain
    /// reload destroys the bridge at a moment nobody chose. The adoption case is
    /// worse, because adoption rotates: if its reply is lost, the old secret is
    /// already dead and the replacement was only ever in the reply that vanished.
    ///
    /// So the client generates a 32-byte secret locally and sends only its
    /// verifier. It possesses the credential <em>before</em> the side effect, and
    /// the authority needed after a lost reply never existed only inside that
    /// lost reply.
    ///
    /// No salt. The secret is 256 bits from a CSPRNG, and a salt defends against
    /// precomputation over low-entropy secrets, which this is not. Dropping it is
    /// what lets a client compute the same verifier the host will store, which is
    /// what makes precommitment possible at all.
    /// </remarks>
    internal sealed class RoboVisionRecoveryToken
    {
        private const int SecretBytes = 32;

        private RoboVisionRecoveryToken(string verifier, bool precommitted)
        {
            Verifier = verifier;
            Precommitted = precommitted;
        }

        internal string Verifier { get; }

        /// <summary>Whether the client held this credential before the side effect.</summary>
        /// <remarks>
        /// Reported so the weaker path is visible rather than indistinguishable
        /// from the safe one.
        /// </remarks>
        internal bool Precommitted { get; }

        internal static RoboVisionRecoveryToken Precommit(string verifier)
        {
            return String.IsNullOrEmpty(verifier) ? null : new RoboVisionRecoveryToken(verifier, true);
        }

        /// <summary>Fallback for a caller that did not precommit. Returns the secret once.</summary>
        internal static RoboVisionRecoveryToken Mint(out string secret)
        {
            var token = new byte[SecretBytes];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(token);
            secret = Encode(token);
            return new RoboVisionRecoveryToken(VerifierFor(secret), false);
        }

        /// <summary>Rebuild from what was stashed across a domain reload.</summary>
        internal static RoboVisionRecoveryToken FromStored(string verifier, bool precommitted)
        {
            if (String.IsNullOrEmpty(verifier)) return null;
            return new RoboVisionRecoveryToken(verifier, precommitted);
        }

        /// <summary>The value the host stores. Computable by the client, which is the point.</summary>
        internal static string VerifierFor(string secret)
        {
            using (var sha = SHA256.Create())
            {
                return Encode(sha.ComputeHash(Encoding.UTF8.GetBytes(secret)));
            }
        }

        /// <summary>Constant-time check. Never reports which half of the answer was wrong.</summary>
        internal bool Matches(string candidate)
        {
            if (String.IsNullOrEmpty(candidate)) return false;
            var offered = Encoding.UTF8.GetBytes(VerifierFor(candidate));
            var stored = Encoding.UTF8.GetBytes(Verifier);
            if (offered.Length != stored.Length) return false;
            var difference = 0;
            for (var i = 0; i < stored.Length; i++) difference |= offered[i] ^ stored[i];
            return difference == 0;
        }

        // URL-safe, unpadded: these travel in JSON and turn up in client logs and
        // shell history, where '+' and '/' are a nuisance.
        private static string Encode(byte[] bytes)
        {
            return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        }
    }
}
