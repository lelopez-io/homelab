// Probe application for the failover gate. The writer and verifier in this
// directory speak only the three routes below; the stub fleet implements the
// same contract for local demonstration.
export class GateProbe {
  constructor(state) {
    this.state = state;
  }

  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === "/write" && request.method === "POST") {
      const body = await request.json();
      const key = "w/" + String(body.seq).padStart(10, "0");
      await this.state.storage.put(key, body.value);
      // The response is the acknowledgement under test: the runtime holds
      // each write response until a durability proof covers the put, so a
      // client that receives this answer was told the write survives a
      // failure. No storage.sync() here; the output gate is the mechanism
      // the gate exists to measure.
      return Response.json({ seq: body.seq, ok: true });
    }

    if (url.pathname === "/read" && request.method === "GET") {
      const seq = url.searchParams.get("seq");
      const key = "w/" + String(seq).padStart(10, "0");
      const value = await this.state.storage.get(key);
      if (value === undefined) {
        return Response.json({ seq: Number(seq), found: false }, { status: 404 });
      }
      return Response.json({ seq: Number(seq), found: true, value });
    }

    if (url.pathname === "/dump" && request.method === "GET") {
      const writes = await this.state.storage.list({ prefix: "w/" });
      return Response.json({ keys: [...writes.keys()] });
    }

    return Response.json({ error: "unknown route" }, { status: 404 });
  }
}

export default {
  async fetch(request, env) {
    // One fixed cell means one owner, so each scenario knows exactly which
    // cell the fleet must fail over and which process owns it.
    return env.GATE_PROBE.getByName("gate-probe").fetch(request);
  },
};
