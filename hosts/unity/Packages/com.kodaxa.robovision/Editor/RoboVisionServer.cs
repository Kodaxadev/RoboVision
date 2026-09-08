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
            public readonly Socket Socket;
            public readonly List<byte> Inbound = new List<byte>();
            public readonly List<byte> Outbound = new List<byte>();

            public ClientState(Socket socket) { Socket = socket; }
        }

        private readonly List<ClientState> _clients = new List<ClientState>();
        private Socket _listener;
        private readonly Func<JObject, JObject> _dispatch;

        public int Port { get; }
        public bool Running => _listener != null;

        public RoboVisionServer(int port, Func<JObject, JObject> dispatch)
        {
            Port = port;
            _dispatch = dispatch ?? throw new ArgumentNullException(nameof(dispatch));
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
                    _clients.Add(new ClientState(socket));
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
                    response = _dispatch(request);
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
            try { _clients[index].Socket.Dispose(); } catch { }
            _clients.RemoveAt(index);
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
