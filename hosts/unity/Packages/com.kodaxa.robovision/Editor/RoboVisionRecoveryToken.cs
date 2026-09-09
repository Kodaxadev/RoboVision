using System;
using System.Security.Cryptography;
using System.Text;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// Proof that a client is the one that opened a transaction.
    /// </summary>
    /// <remarks>
    /// A dropped TCP connection is not authorization. The previous rule — any
    /// client may finish an orphan once the owner disconnects — let anything
    /// that could reach the port commit or roll back someone else's half-written
    /// edit, which is a weaker claim than the transaction guarantee is worth.
    ///
    /// So the owner is handed one secret at begin, and it is the only thing that
    /// can take ownership back. The host keeps a salted hash, never the token:
    /// a verifier that leaks proves nothing, and there is no code path that can
    /// return the token a second time.
    /// </remarks>
    internal sealed class RoboVisionRecoveryToken
    {
        private const int TokenBytes = 32;
        private const int SaltBytes = 16;

        private RoboVisionRecoveryToken(string salt, string verifier)
        {
            Salt = salt;
            Verifier = verifier;
        }

        internal string Salt { get; }
        internal string Verifier { get; }

        /// <summary>Mint a token, returning the secret once and keeping only its verifier.</summary>
        internal static RoboVisionRecoveryToken Mint(out string secret)
        {
            var token = new byte[TokenBytes];
            var salt = new byte[SaltBytes];
            using (var rng = RandomNumberGenerator.Create())
            {
                rng.GetBytes(token);
                rng.GetBytes(salt);
            }
            secret = Encode(token);
            var saltText = Encode(salt);
            return new RoboVisionRecoveryToken(saltText, Verify(saltText, secret));
        }

        /// <summary>Rebuild a verifier from what was stashed across a domain reload.</summary>
        internal static RoboVisionRecoveryToken FromStored(string salt, string verifier)
        {
            if (String.IsNullOrEmpty(salt) || String.IsNullOrEmpty(verifier)) return null;
            return new RoboVisionRecoveryToken(salt, verifier);
        }

        /// <summary>Constant-time check. Never reports which half of the answer was wrong.</summary>
        internal bool Matches(string candidate)
        {
            if (String.IsNullOrEmpty(candidate)) return false;
            var offered = Encoding.UTF8.GetBytes(Verify(Salt, candidate));
            var stored = Encoding.UTF8.GetBytes(Verifier);
            if (offered.Length != stored.Length) return false;
            var difference = 0;
            for (var i = 0; i < stored.Length; i++) difference |= offered[i] ^ stored[i];
            return difference == 0;
        }

        private static string Verify(string salt, string secret)
        {
            using (var sha = SHA256.Create())
            {
                return Encode(sha.ComputeHash(Encoding.UTF8.GetBytes(salt + ":" + secret)));
            }
        }

        // URL-safe, unpadded: a token travels in JSON and turns up in client
        // logs and shell history, where '+' and '/' are a nuisance.
        private static string Encode(byte[] bytes)
        {
            return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        }
    }
}
