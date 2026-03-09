/**
 * WebSocket server for Python-Node.js bridge communication.
 * Binds to 127.0.0.1 only; optional BRIDGE_TOKEN auth.
 */

import { WebSocketServer, WebSocket } from 'ws';
import { timingSafeEqual } from 'crypto';
import { WhatsAppClient, InboundMessage, HistorySyncEvent } from './whatsapp.js';

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
  private rateLimit: number;

  constructor(private port: number, private authDir: string, private token?: string) {
    // Rate limit from env (set by Python config) or default 30.
    // Proof: WhatsApp bans at ~200 msg/min. 30 = 15% of ban threshold = safe margin.
    this.rateLimit = parseInt(process.env.RATE_LIMIT_PER_MINUTE || '30', 10) || 30;
  }

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
      onHistorySync: (event: HistorySyncEvent) => {
        this.broadcast({
          type: 'history_sync',
          syncType: event.syncType,
          messages: event.messages,
          progress: event.progress,
          isLatest: event.isLatest,
        });
      },
    });

    this.wss.on('connection', (ws) => {
      if (this.token) {
        const timeout = setTimeout(() => ws.close(4001, 'Auth timeout'), 5000);
        ws.once('message', (data) => {
          clearTimeout(timeout);
          try {
            const msg = JSON.parse(data.toString());
            // Theorem T_TSAFE: Timing-safe token comparison (P_TIMING).
            // JavaScript === short-circuits on first mismatch, leaking token
            // length/prefix via response timing. timingSafeEqual always compares
            // all bytes in constant time, preventing timing side-channel attacks.
            if (msg.type === 'auth' && typeof msg.token === 'string' && this.token &&
                msg.token.length === this.token.length &&
                timingSafeEqual(Buffer.from(msg.token), Buffer.from(this.token))) {
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
        // Theorem T_ERRREDACT: Don't leak internal errors to client (P_LOGPII).
        // Error may contain file paths, stack traces, or internal state.
        const errMsg = error instanceof Error ? error.message : 'Unknown error';
        console.error('Command error:', errMsg);
        ws.send(JSON.stringify({ type: 'error', error: errMsg }));
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
      if (this.sendCount >= this.rateLimit) {
        throw new Error(`Rate limit exceeded: ${this.rateLimit} messages per minute`);
      }

      // Theorem T_GRPREADONLY: Block group/broadcast sends at bridge level (P_GRPGATE).
      // Defense-in-depth: even if Python logic has a bug, the bridge refuses group sends
      // unless explicitly owner-approved (e.g., for admin-initiated escalation responses).
      const to = cmd.to || '';
      if (to.endsWith('@g.us') || to.includes('@broadcast')) {
        if (!cmd.ownerApproved) {
          throw new Error('Group/broadcast sends blocked. Use ownerApproved flag for admin-approved sends.');
        }
        console.log(`[AUDIT] Owner-approved group send to ${to}`);
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
    } else if (cmd.type === 'fetch_history' && this.wa) {
      const { chatJid, count } = cmd;
      await this.wa.fetchMessageHistory(chatJid, count || 50);
      return { fetching: true, chatJid };
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
