using System;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// The undo mechanics a checkpoint is made of, separate from who is allowed to use one.
    /// </summary>
    /// <remarks>
    /// Ownership and lifecycle decide <em>whether</em> a rollback may run;
    /// everything here decides whether it can, and proves it afterwards by
    /// comparing fingerprints rather than trusting that Undo did the right
    /// thing. Keeping the two apart matters because they fail for unrelated
    /// reasons: an unauthorised caller and an undo stack that cannot reach the
    /// checkpoint are different problems with different answers.
    /// </remarks>
    internal static class RoboVisionUndo
    {
        internal sealed class OperationCheckpoint
        {
            public int UndoGroup;
            public string Fingerprint;
            public string Label;
        }

        /// <summary>Open an undo group for one operation, and remember what it is undoing to.</summary>
        internal static OperationCheckpoint PrepareMutation(string label, string fingerprint = null)
        {
            // The host has already re-read the scene to validate if_revision, so
            // recomputing here would pay for the same hash twice.
            fingerprint = fingerprint ?? RoboVisionSceneRead.ComputeFingerprint();
            Undo.IncrementCurrentGroup();
            var group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("RoboVision OP " + label);
            return new OperationCheckpoint { UndoGroup = group, Fingerprint = fingerprint, Label = label };
        }

        /// <summary>Open the undo group a transaction will be rolled back to.</summary>
        internal static int OpenGroup(string label)
        {
            Undo.IncrementCurrentGroup();
            var group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("RoboVision " + label);
            return group;
        }

        internal static void Collapse(int group)
        {
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(group);
        }

        /// <summary>Revert to a group and report the fingerprint that resulted.</summary>
        internal static string RevertTo(int group)
        {
            Undo.FlushUndoRecordObjects();
            Undo.RevertAllDownToGroup(group);
            return RoboVisionSceneRead.ComputeFingerprint();
        }

        /// <summary>Undo a failed operation, or say that the scene could not be put back.</summary>
        internal static JObject RecoverFailedMutation(OperationCheckpoint checkpoint)
        {
            var current = RoboVisionSceneRead.ComputeFingerprint();
            if (String.Equals(current, checkpoint.Fingerprint, StringComparison.Ordinal))
                return new JObject
                {
                    ["recovered"] = true,
                    ["undo_group"] = checkpoint.UndoGroup,
                    ["fingerprint"] = checkpoint.Fingerprint,
                    ["undo_performed"] = false
                };

            current = RevertTo(checkpoint.UndoGroup);
            if (!String.Equals(current, checkpoint.Fingerprint, StringComparison.Ordinal))
                throw new RoboVisionException(
                    "MUTATION_RECOVERY_INCOMPLETE",
                    "failed Unity operation changed editor state and Undo did not restore the pre-operation fingerprint",
                    data: new JObject
                    {
                        ["operation"] = checkpoint.Label,
                        ["undo_group"] = checkpoint.UndoGroup,
                        ["expected_fingerprint"] = checkpoint.Fingerprint,
                        ["actual_fingerprint"] = current
                    });

            return new JObject
            {
                ["recovered"] = true,
                ["undo_group"] = checkpoint.UndoGroup,
                ["fingerprint"] = checkpoint.Fingerprint,
                ["undo_performed"] = true
            };
        }
    }
}
