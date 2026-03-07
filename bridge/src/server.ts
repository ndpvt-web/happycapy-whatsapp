/**
 * WebSocket server for Python-Node.js bridge communication.
 * Binds to 127.0.0.1 only; optional BRIDGE_TOKEN auth.
 */

import { WebSocketServer, WebSocket } from 'ws';
import { WhatsAppClient, InboundMessage } from './whatsapp.js';

interface SendCommand {
  type: 'send';
  to: string;
  text: string;
  media?: {
    data: string;      // base64
    mimetype: string;
    filename: string;
  };
}

interface DeleteCommand {
  type: 'delete';
  remoteJid: string;
  msgId: string;
  fromMe: boolean;
  participant?: string;
}

interface BridgeMessage {
  type: string;
  [key: string]: unknown;
}

export class BridgeServer {
  private wss: WebSocketServer | null = null;
  private wa: WhatsAppClient | null = null;
  private clients: Set<WebSocket> = new Set();
  private sendCount = 0;
  private sendCountReset: NodeJS.Timeout | null = null;

  constructor(private port: number, private authDir: string, private token?: string) {}

  async start(): Promise<void> {
    this.wss = new WebSocketServer({ host: '127.0.0.1', port: this.port });
    console.log(`Bridge server listening on ws://127.0.0.1:${this.port}`);
    if (this.token) console.log('Token authentication enabled');

    // Rate limit reset every 60s
    this.sendCountReset = setInterval(() => { this.sendCount = 0; }, 60000);

    this.wa = new WhatsAppClient({
      authDir: this.authDir,
      onMessage: (msg) => this.broadcast({ type: 'message', ...msg }),
      onQR: (qr) => {
        // Emit to stdout for Python capture
        console.log(`QR:${qr}`);
        this.broadcast({ type: 'qr', qr });
      },
      onStatus: (status) => {
        console.log(`STATUS:${status}`);
        this.broadcast({ type: 'status', status });
      },
    });

    this.wss.on('connection', (ws) => {
      if (this.token) {
        const timeout = setTimeout(() => ws.close(4001, 'Auth timeout'), 5000);
        ws.once('message', (data) => {
          clearTimeout(timeout);
          try {
            const msg = JSON.parse(data.toString());
            if (msg.type === 'auth' && msg.token === this.token) {
              console.log('Python client authenticated');
              this.setupClient(ws);
            } else {
              ws.close(4003, 'Invalid token');
            }
          } catch {
            ws.close(4003, 'Invalid auth message');
          }
        });
      } else {
        console.log('Python client connected');
        this.setupClient(ws);
      }
    });

    await this.wa.connect();
  }

  private setupClient(ws: WebSocket): void {
    this.clients.add(ws);

    ws.on('message', async (data) => {
      try {
        const cmd = JSON.parse(data.toString());
        const result = await this.handleCommand(cmd);
        ws.send(JSON.stringify({ type: 'sent', ...result }));
      } catch (error) {
        console.error('Error handling command:', error);
        ws.send(JSON.stringify({ type: 'error', error: String(error) }));
      }
    });

    ws.on('close', () => {
      console.log('Python client disconnected');
      this.clients.delete(ws);
    });

    ws.on('error', (error) => {
      console.error('WebSocket error:', error);
      this.clients.delete(ws);
    });
  }

  private async handleCommand(cmd: any): Promise<Record<string, unknown>> {
    if (cmd.type === 'send' && this.wa) {
      // Rate limiting
      if (this.sendCount >= 30) {
        throw new Error('Rate limit exceeded: 30 messages per minute');
      }

      // Block group sends
      const to = cmd.to || '';
      if (to.endsWith('@g.us') || to.includes('@broadcast')) {
        throw new Error('Group/broadcast sends blocked for safety');
      }

      this.sendCount++;

      if (cmd.media) {
        const result = await this.wa.sendMedia(to, cmd.media.data, cmd.media.mimetype, cmd.media.filename);
        return { to, messageId: result?.key?.id };
      } else {
        const result = await this.wa.sendMessage(to, cmd.text);
        return { to, messageId: result?.key?.id, remoteJid: result?.key?.remoteJid, fromMe: true };
      }
    } else if (cmd.type === 'delete' && this.wa) {
      await this.wa.deleteMessage(cmd.remoteJid, cmd.msgId, cmd.fromMe, cmd.participant);
      return { deleted: true, msgId: cmd.msgId };
    }
    return {};
  }

  private broadcast(msg: BridgeMessage): void {
    const data = JSON.stringify(msg);
    for (const client of this.clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send(data);
      }
    }
  }

  async stop(): Promise<void> {
    if (this.sendCountReset) clearInterval(this.sendCountReset);
    for (const client of this.clients) client.close();
    this.clients.clear();
    if (this.wss) { this.wss.close(); this.wss = null; }
    if (this.wa) { await this.wa.disconnect(); this.wa = null; }
  }
}
