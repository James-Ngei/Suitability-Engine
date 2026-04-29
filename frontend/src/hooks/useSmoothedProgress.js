/**
 * useSmoothedProgress.js
 * ----------------------
 * A hook that produces a "always-moving" progress value for long background tasks.
 *
 * Behaviour:
 *   - While status is 'fetching' or 'pipeline', simulates steady progress:
 *       0 → 78%  over ~8 minutes (slows as it approaches the ceiling)
 *       Never gets stuck — always creeps forward
 *   - Whenever the real server pct exceeds the simulated value, jumps to real value
 *   - When status = 'ready', snaps to 100% with a short animation delay
 *   - When status = 'error' or idle, resets to 0
 *
 * Usage:
 *   const { displayPct, displayMsg } = useSmoothedProgress(status, realPct, message);
 */

import { useState, useEffect, useRef } from 'react';

// Messages shown at each phase — cycle through them to feel alive
const PHASE_MESSAGES = {
  fetching: [
    'Connecting to Planetary Computer…',
    'Locating satellite data tiles…',
    'Downloading elevation (COP-DEM)…',
    'Fetching rainfall data (NASA POWER)…',
    'Fetching temperature data…',
    'Downloading soil data (SoilGrids)…',
    'Deriving slope from elevation…',
    'Assembling raster layers…',
    'Verifying downloaded tiles…',
    'Finalising raw data…',
  ],
  pipeline: [
    'Reprojecting to WGS-84…',
    'Clipping to county boundary…',
    'Aligning pixel grids…',
    'Snapping to boundary extent…',
    'Applying fuzzy membership functions…',
    'Normalising elevation layer…',
    'Normalising rainfall layer…',
    'Normalising soil layer…',
    'Clipping normalised layers…',
    'Building constraints mask…',
    'Loading layers into memory…',
    'Almost ready…',
  ],
};

// How fast the simulated bar moves:
// At pct=0 it advances ~0.8%/tick, slows exponentially toward the ceiling
const TICK_MS        = 1500;   // tick every 1.5s
const CEILING        = 78;     // never exceed this via simulation alone
const INITIAL_SPEED  = 0.9;    // % per tick at pct=0
const DECAY          = 0.045;  // how fast speed falls off (higher = slower approach to ceiling)

function simulatedIncrement(currentPct) {
  // Exponential decay: speed = INITIAL_SPEED * e^(-DECAY * currentPct)
  // This produces smooth deceleration that never quite reaches CEILING
  const speed = INITIAL_SPEED * Math.exp(-DECAY * currentPct);
  return Math.max(speed, 0.04); // minimum creep so it never fully stops
}

export function useSmoothedProgress(status, realPct, message) {
  const [displayPct, setDisplayPct] = useState(0);
  const [displayMsg, setDisplayMsg] = useState('');
  const [msgIndex,   setMsgIndex]   = useState(0);

  const tickRef    = useRef(null);
  const msgRef     = useRef(null);
  const isActive   = status === 'fetching' || status === 'pipeline';
  const prevStatus = useRef(status);

  // Reset when a new load starts (status transitions from idle/ready/error → fetching)
  useEffect(() => {
    const wasInactive = prevStatus.current !== 'fetching' && prevStatus.current !== 'pipeline';
    if (isActive && wasInactive) {
      setDisplayPct(0);
      setMsgIndex(0);
      setDisplayMsg(PHASE_MESSAGES.fetching[0]);
    }
    prevStatus.current = status;
  }, [status, isActive]);

  // Simulated tick — advances the bar steadily
  useEffect(() => {
    if (!isActive) {
      if (tickRef.current) clearInterval(tickRef.current);
      tickRef.current = null;
      return;
    }

    tickRef.current = setInterval(() => {
      setDisplayPct(prev => {
        // If server reported a higher value, use that
        const serverVal = typeof realPct === 'number' ? realPct : 0;
        const base      = Math.max(prev, serverVal);

        if (base >= CEILING) return base; // simulation ceiling reached — wait for server
        const inc = simulatedIncrement(base);
        return Math.min(base + inc, CEILING);
      });
    }, TICK_MS);

    return () => { if (tickRef.current) clearInterval(tickRef.current); };
  }, [isActive, realPct]);

  // Always prefer server value if it's higher
  useEffect(() => {
    if (typeof realPct === 'number' && realPct > 0) {
      setDisplayPct(prev => Math.max(prev, realPct));
    }
  }, [realPct]);

  // Snap to 100 on ready
  useEffect(() => {
    if (status === 'ready') {
      // Small delay so the bar is visible completing
      const t = setTimeout(() => setDisplayPct(100), 300);
      return () => clearTimeout(t);
    }
    if (status === 'error') {
      setDisplayPct(0);
    }
  }, [status]);

  // Rotate messages every ~6 seconds
  useEffect(() => {
    if (!isActive) {
      if (msgRef.current) clearInterval(msgRef.current);
      return;
    }

    const msgs = status === 'pipeline' ? PHASE_MESSAGES.pipeline : PHASE_MESSAGES.fetching;

    msgRef.current = setInterval(() => {
      setMsgIndex(i => {
        const next = (i + 1) % msgs.length;
        setDisplayMsg(msgs[next]);
        return next;
      });
    }, 6000);

    // Set initial message for this phase
    const msgs2 = status === 'pipeline' ? PHASE_MESSAGES.pipeline : PHASE_MESSAGES.fetching;
    setDisplayMsg(prev => prev || msgs2[0]);

    return () => { if (msgRef.current) clearInterval(msgRef.current); };
  }, [isActive, status]);

  // If server sends a non-empty message that isn't the generic one, prefer it
  const finalMsg = (message && message.length > 3 && !message.includes('Starting'))
    ? message
    : displayMsg;

  return {
    displayPct: Math.round(displayPct),
    displayMsg: status === 'ready'  ? 'Ready — running analysis…'
               : status === 'error' ? 'Data fetch failed'
               : finalMsg,
    isComplete: status === 'ready',
    isError:    status === 'error',
  };
}