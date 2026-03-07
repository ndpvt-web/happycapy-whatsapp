#!/usr/bin/env node
/**
 * HappyCapy WhatsApp Bridge
 * Connects WhatsApp Web to Python orchestrator via WebSocket.
 */

import { webcrypto } from 'crypto';
if (!globalThis.crypto) {
  (globalThis as any).crypto = webcrypto;
}

import { BridgeServer } from './server.js';
import { homedir } from 'os';
import { join } from 'path';

const PORT = parseInt(process.env.BRIDGE_PORT || '3002', 10);
const AUTH_DIR = process.env.AUTH_DIR || join(homedir(), '.happycapy-whatsapp', 'whatsapp-auth');
const TOKEN = process.env.BRIDGE_TOKEN || undefined;

console.log('HappyCapy WhatsApp Bridge');
console.log('========================');

const server = new BridgeServer(PORT, AUTH_DIR, TOKEN);

process.on('SIGINT', async () => {
  console.log('Shutting down...');
  await server.stop();
  process.exit(0);
});

process.on('SIGTERM', async () => {
  await server.stop();
  process.exit(0);
});

server.start().catch((error) => {
  console.error('Failed to start bridge:', error);
  process.exit(1);
});
