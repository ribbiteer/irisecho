<!-- SPDX-License-Identifier: AGPL-3.0-or-later
  Settings card for phone and network access: off by default, pairing by QR code,
  and the list of paired devices. -->
<script lang="ts">
  import QrCode from "@lucide/svelte/icons/qr-code";
  import Smartphone from "@lucide/svelte/icons/smartphone";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { api } from "../lib/api";
  import { ago } from "../lib/format";
  import { fail, toast } from "../lib/state.svelte";
  import type { LanStatus, PairCode } from "../lib/types";

  let lan = $state<LanStatus | null>(null);
  let pairing = $state<PairCode | null>(null);
  let left = $state(0);
  let busy = $state(false);
  let deviceCount = 0;

  async function load() {
    try {
      lan = await api<LanStatus>("/lan");
    } catch (e) {
      fail(e);
    }
  }

  $effect(() => {
    load();
    // Keep the list of devices fresh, and notice when the phone has paired.
    const timer = setInterval(async () => {
      if (!lan?.active) return;
      await load();
      if (pairing && lan && lan.devices.length > deviceCount) {
        toast(`Paired ${lan.devices[0].name}.`, "ok");
        pairing = null;
      }
    }, 2000);
    return () => clearInterval(timer);
  });

  // Count down the code's life, and hide it when it ends.
  $effect(() => {
    if (!pairing) return;
    left = pairing.expires_in;
    const timer = setInterval(() => {
      left -= 1;
      if (left <= 0) pairing = null;
    }, 1000);
    return () => clearInterval(timer);
  });

  async function toggle() {
    if (!lan || busy) return;
    busy = true;
    pairing = null;
    try {
      lan = await api<LanStatus>("/lan", { body: { enabled: !lan.enabled } });
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
    }
  }

  async function pair() {
    try {
      deviceCount = lan?.devices.length ?? 0;
      pairing = await api<PairCode>("/lan/pair-code", { body: {} });
    } catch (e) {
      fail(e);
    }
  }

  async function revoke(id: string, name: string) {
    try {
      await api(`/lan/devices/${id}`, { method: "DELETE" });
      toast(`${name} can no longer connect.`, "ok");
      await load();
    } catch (e) {
      fail(e);
    }
  }

  const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
</script>

<section class="card">
  <h2>Phone and network access</h2>
  <div class="setting">
    <div class="what">
      <strong>Let other devices on my network connect</strong>
      <small>
        Off by default. When on, a phone or tablet on the same Wi-Fi can make things and browse your library after you
        pair it here with a one-time code. Models, settings and folders stay on this computer.
      </small>
    </div>
    <button class="switch" role="switch" aria-checked={lan?.enabled ?? false} aria-label="Allow other devices" disabled={!lan || busy} onclick={toggle}
    ></button>
  </div>

  {#if lan?.enabled}
    {#if lan.error}
      <p class="note warn">{lan.error}</p>
    {:else if lan.active}
      <div class="notice" role="note">
        <TriangleAlert size={16} />
        <p>
          This connection is plain HTTP: it is not encrypted, so anyone on your network who can watch traffic could see
          what your phone sees. Use it at home or on a network you trust. A paired device can be revoked below at any time.
        </p>
      </div>

      <div class="setting">
        <div class="what">
          <strong>Address</strong>
          <small>Your phone opens this once it is paired. If it cannot connect, allow IrisEcho through the firewall for private networks.</small>
        </div>
        <div class="value col">
          {#each lan.urls as url (url)}<span class="mono path">{url}</span>{:else}<span class="dim">No network found.</span>{/each}
        </div>
      </div>

      {#if pairing}
        <div class="pair" role="region" aria-label="Pair a phone">
          <div class="qr" aria-label="QR code to pair a phone">{@html pairing.qr}</div>
          <div class="steps">
            <p><strong>1.</strong> Point your phone's camera at the code, or open <span class="mono">{pairing.address}</span></p>
            <p><strong>2.</strong> If asked, type this code:</p>
            <p class="code mono">{pairing.code.slice(0, 4)} {pairing.code.slice(4)}</p>
            <p class="dim">Works once, for {clock(left)} more.</p>
            <button class="btn sm ghost" onclick={() => (pairing = null)}>Cancel</button>
          </div>
        </div>
      {:else}
        <div>
          <button class="btn primary" onclick={pair}><QrCode size={16} /> Pair a phone</button>
        </div>
      {/if}

      <div class="devices">
        <h3>Paired devices</h3>
        {#each lan.devices as d (d.id)}
          <div class="device">
            <Smartphone size={18} />
            <div class="who">
              <strong>{d.name}</strong>
              <small>Paired {ago(d.created)} · last seen {ago(d.last_seen)}</small>
            </div>
            <button class="btn sm ghost danger" onclick={() => revoke(d.id, d.name)}>Revoke</button>
          </div>
        {:else}
          <p class="note">No devices are paired.</p>
        {/each}
      </div>
    {/if}
  {/if}
</section>

<style>
  .card {
    padding: 20px 22px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  h2 {
    font-size: 17px;
  }
  h3 {
    font-size: 13.5px;
    color: var(--text-2);
    margin-bottom: 6px;
  }
  .setting {
    display: flex;
    align-items: center;
    gap: 24px;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .what {
    display: flex;
    flex-direction: column;
    gap: 3px;
    flex: 1 1 320px;
  }
  .what small,
  .note,
  .dim {
    color: var(--text-3);
    font-size: 12.5px;
  }
  .note.warn {
    color: var(--amber-ink);
  }
  .value.col {
    display: flex;
    flex-direction: column;
    gap: 4px;
    align-items: flex-end;
  }
  .path {
    font-size: 12.5px;
    color: var(--text-2);
  }
  .notice {
    display: flex;
    gap: 10px;
    padding: 12px 14px;
    border-radius: var(--r-2);
    border: 1px solid color-mix(in oklab, var(--amber) 35%, transparent);
    background: color-mix(in oklab, var(--amber) 8%, transparent);
    color: var(--amber-ink);
    font-size: 12.5px;
  }
  .notice :global(svg) {
    flex: none;
    margin-top: 1px;
  }
  .pair {
    display: flex;
    gap: 22px;
    flex-wrap: wrap;
    align-items: center;
    padding: 16px;
    border-radius: var(--r-3);
    border: 1px solid var(--line-2);
    background: var(--surface-2);
  }
  .qr {
    width: 190px;
    height: 190px;
    background: #fff;
    border-radius: 12px;
    padding: 8px;
  }
  .qr :global(svg) {
    width: 100%;
    height: 100%;
    display: block;
  }
  .steps {
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
    font-size: 13.5px;
    min-width: 220px;
    flex: 1;
  }
  .code {
    font-size: 30px;
    letter-spacing: 0.12em;
    color: var(--text);
  }
  .device {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 0;
    border-top: 1px solid var(--line);
  }
  .who {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
  }
  .who small {
    color: var(--text-3);
    font-size: 12px;
  }
</style>
