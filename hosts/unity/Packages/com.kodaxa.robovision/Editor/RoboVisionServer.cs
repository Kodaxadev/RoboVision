using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    internal sealed class RoboVisionServer : IDisposable
    {
        private const int MaxMessageBytes = 4 * 1024 * 1024;

        private sealed class ClientState
        {
            public readonly long Id;
            public readonly Socket Socket;
            public readonly List<byte> Inbound = new List<byte>();
            public readonly List<byte> Outbound = new List<byte>();

            public ClientState(long id, Socket socket) { Id = id; Socket = socket; }
        }

        private readonly List<ClientState> _clients = new List<ClientState>();
        // Set by a fault seam so the close path can skip flushing a response the
        // test has just decided the caller never sees.
        private bool _dropped;
        private Socket _listener;
        // Requests carry the connection they arrived on so the host can apply a
        // concurrency policy instead of treating every client as the same caller.
        private readonly Func<JObject, long, JObject> _dispatch;
        private readonly Action<long> _onClientClosed;
        private long _nextClientId;

        public int Port { get; }
        public bool Running => _listener != null;

        /// <summary>A test seam for the one window that cannot otherwise be reached.</summary>
        /// <remarks>
        /// After the host has applied a request and before its response is
        /// delivered. Every acknowledgement-loss guarantee is about exactly that
        /// gap, and a test that reached it by editing private state afterwards
        /// would be asserting its own edit rather than the host's behaviour.
        /// Called with the request and the response; returning false drops the
        /// response and closes the connection. Always null in a shipped host —
        /// nothing in the Editor assembly ever sets it.
        /// </remarks>
        internal Func<JObject, JObject, bool> FaultAfterDispatch { get; set; }

        /// <summary>The other half of the same seam: a request that never arrived.</summary>
        /// <remarks>
        /// Telling "applied but unacknowledged" apart from "never applied" is
        /// exactly what the credential generation counter exists for, so both
        /// sides of that distinction have to be reachable deterministically.
        /// </remarks>
        internal Func<JObject, bool> FaultBeforeDispatch { get; set; }

        public RoboVisionServer(int port, Func<JObject, long, JObject> dispatch, Action<long> onClientClosed = null)
        {
            Port = port;
            _dispatch = dispatch ?? throw new ArgumentNullException(nameof(dispatch));
            _onClientClosed = onClientClosed;
        }

        public void Start()
        {
            if (Running) return;
            _listener = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
            _listener.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
            _listener.Bind(new IPEndPoint(IPAddress.Loopback, Port));
            _listener.Listen(16);
            _listener.Blocking = false;
        }

        public void Poll(int commandBudget = 8)
        {
            if (!Running) return;
            AcceptPending();
            var remaining = commandBudget;
            for (var i = _clients.Count - 1; i >= 0 && remaining > 0; i--)
            {
                var client = _clients[i];
                if (!ReadClient(client, ref remaining))
                {
                    // A dropped response is discarded rather than delivered on
                    // the way out; the socket dies with whatever it was holding.
                    if (_dropped) { client.Outbound.Clear(); _dropped = false; }
                    CloseClient(i);
                    continue;
                }
                if (!FlushClient(client)) CloseClient(i);
            }
        }

        private void AcceptPending()
        {
            for (var i = 0; i < 8; i++)
            {
                try
                {
                    if (!_listener.Poll(0, SelectMode.SelectRead)) return;
                    var socket = _listener.Accept();
                    socket.Blocking = false;
                    _nextClientId++;
                    _clients.Add(new ClientState(_nextClientId, socket));
                }
                catch (SocketException ex) when (ex.SocketErrorCode == SocketError.WouldBlock)
                {
                    return;
                }
            }
        }

        private bool ReadClient(ClientState client, ref int budget)
        {
            try
            {
                while (client.Socket.Available > 0)
                {
                    var buffer = new byte[Math.Min(65536, client.Socket.Available)];
                    var count = client.Socket.Receive(buffer);
                    if (count <= 0) return false;
                    for (var i = 0; i < count; i++) client.Inbound.Add(buffer[i]);
                    if (client.Inbound.Count > MaxMessageBytes && !client.Inbound.Contains((byte)'\n'))
                    {
                        Queue(client, TransportError("MESSAGE_TOO_LARGE", "request exceeds transport limit"));
                        return false;
                    }
                }

                if (client.Socket.Poll(0, SelectMode.SelectRead) && client.Socket.Available == 0)
                    return false;
            }
            catch (SocketException ex) when (ex.SocketErrorCode == SocketError.WouldBlock) { }

            while (budget > 0)
            {
                var newline = client.Inbound.IndexOf((byte)'\n');
                if (newline < 0) break;
                var raw = client.Inbound.GetRange(0, newline).ToArray();
                client.Inbound.RemoveRange(0, newline + 1);
                if (raw.Length == 0) continue;

                JObject response;
                try
                {
                    var text = Encoding.UTF8.GetString(raw);
                    var request = JObject.Parse(text);
                    if (FaultBeforeDispatch != null && !FaultBeforeDispatch(request))
                    {
                        // Never reached the host.
                        _dropped = true;
                        return false;
                    }
                    response = _dispatch(request, client.Id);
                    if (FaultAfterDispatch != null && !FaultAfterDispatch(request, response))
                    {
                        // Applied by the host, never seen by the caller.
                        _dropped = true;
                        return false;
                    }
                }
                catch (Exception ex) when (ex is JsonException || ex is DecoderFallbackException)
                {
                    response = TransportError("INVALID_REQUEST", ex.Message);
                }
                Queue(client, response);
                budget--;
            }
            return true;
        }

        private static JObject TransportError(string code, string message)
        {
            return new JObject
            {
                ["rv"] = "1.0", ["id"] = "invalid", ["ok"] = false, ["revision"] = 0,
                ["error"] = new JObject { ["code"] = code, ["message"] = message, ["retryable"] = false }
            };
        }

        private static void Queue(ClientState client, JObject payload)
        {
            var bytes = Encoding.UTF8.GetBytes(payload.ToString(Formatting.None) + "\n");
            client.Outbound.AddRange(bytes);
        }

        private static bool FlushClient(ClientState client)
        {
            if (client.Outbound.Count == 0) return true;
            try
            {
                var bytes = client.Outbound.ToArray();
                var sent = client.Socket.Send(bytes);
                if (sent > 0) client.Outbound.RemoveRange(0, sent);
                return true;
            }
            catch (SocketException ex) when (ex.SocketErrorCode == SocketError.WouldBlock)
            {
                return true;
            }
            catch (SocketException)
            {
                return false;
            }
        }

        private void CloseClient(int index)
        {
            var client = _clients[index];
            try { client.Socket.Dispose(); } catch { }
            _clients.RemoveAt(index);
            // A client that vanishes while holding a transaction must not leave
            // the host wedged, so the host is told rather than left to guess.
            if (_onClientClosed != null)
            {
                try { _onClientClosed(client.Id); } catch { }
            }
        }

        public void Dispose()
        {
            for (var i = _clients.Count - 1; i >= 0; i--) CloseClient(i);
            if (_listener != null)
            {
                try { _listener.Dispose(); } catch { }
                _listener = null;
            }
        }
    }
}
