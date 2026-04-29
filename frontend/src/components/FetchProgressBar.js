/**
 * FetchProgressBar.js
 * --------------------
 * Reusable progress bar for county data fetching.
 * Uses useSmoothedProgress for the always-moving heartbeat effect.
 *
 * Props:
 *   status   string  — 'idle' | 'fetching' | 'pipeline' | 'ready' | 'error'
 *   realPct  number  — actual % from server (0–100)
 *   message  string  — server status message
 *   compact  bool    — smaller version for inline use (default false)
 */

import React from 'react';
import { useSmoothedProgress } from '../hooks/useSmoothedProgress';

export function FetchProgressBar({ status, realPct, message, compact = false }) {
  const { displayPct, displayMsg, isComplete, isError } = useSmoothedProgress(
    status, realPct, message
  );

  const isActive = status === 'fetching' || status === 'pipeline' || isComplete;
  if (!isActive && !isError) return null;

  // Colour: green normally, amber for pipeline, red for error, bright green on complete
  const barColor = isError    ? '#c05840'
                 : isComplete ? '#2d7a1b'
                 : status === 'pipeline' ? '#5a7a22'
                 : '#3d7a22';

  const bgColor = isError ? '#ffeaea' : '#e8f0df';

  if (compact) {
    return (
      <div style={{ marginTop: '4px' }}>
        <div style={{
          display:        'flex',
          justifyContent: 'space-between',
          alignItems:     'center',
          marginBottom:   '3px',
          gap:            '6px',
        }}>
          <span style={{
            fontSize:     '0.62rem',
            color:        isError ? '#c05840' : '#6a8a58',
            fontStyle:    'italic',
            flex:         1,
            overflow:     'hidden',
            textOverflow: 'ellipsis',
            whiteSpace:   'nowrap',
          }}>
            {displayMsg}
          </span>
          <span style={{
            fontSize:    '0.65rem',
            fontWeight:  700,
            color:       barColor,
            flexShrink:  0,
            fontVariant: 'tabular-nums',
          }}>
            {isError ? '✕' : `${displayPct}%`}
          </span>
        </div>
        <div style={{
          height:       '3px',
          background:   bgColor,
          borderRadius: '2px',
          overflow:     'hidden',
        }}>
          <div style={{
            height:           '100%',
            width:            `${displayPct}%`,
            background:       barColor,
            borderRadius:     '2px',
            transition:       isComplete ? 'width 0.4s ease' : 'width 1.2s ease-out',
            // Subtle shimmer animation on the bar while loading
            backgroundImage:  !isComplete && !isError
              ? `linear-gradient(90deg, ${barColor} 0%, ${barColor}cc 50%, ${barColor} 100%)`
              : 'none',
            backgroundSize:   '200% 100%',
            animation:        !isComplete && !isError ? 'shimmer 2s infinite linear' : 'none',
          }} />
        </div>
        <style>{`
          @keyframes shimmer {
            0%   { background-position: 200% 0; }
            100% { background-position: -200% 0; }
          }
        `}</style>
      </div>
    );
  }

  // Full-size version
  return (
    <div style={{
      marginTop:    '6px',
      background:   bgColor,
      borderRadius: '6px',
      padding:      '8px 10px',
      border:       `1px solid ${isError ? '#f0c8c0' : '#c8dab8'}`,
    }}>
      <div style={{
        display:        'flex',
        justifyContent: 'space-between',
        alignItems:     'center',
        marginBottom:   '5px',
      }}>
        <span style={{
          fontSize:  '0.68rem',
          fontWeight: 600,
          color:     isError ? '#c05840' : '#4a6a38',
        }}>
          {isComplete ? '✓ Data ready' : isError ? '✕ Fetch failed' :
           status === 'pipeline' ? 'Processing data…' : 'Downloading data…'}
        </span>
        <span style={{
          fontSize:    '0.72rem',
          fontWeight:  700,
          color:       barColor,
          fontVariant: 'tabular-nums',
        }}>
          {isError ? '' : `${displayPct}%`}
        </span>
      </div>

      {/* Bar */}
      <div style={{
        height:       '6px',
        background:   'rgba(255,255,255,0.6)',
        borderRadius: '3px',
        overflow:     'hidden',
        marginBottom: '5px',
      }}>
        <div style={{
          height:       '100%',
          width:        `${displayPct}%`,
          background:   barColor,
          borderRadius: '3px',
          transition:   isComplete ? 'width 0.4s ease' : 'width 1.4s ease-out',
        }} />
      </div>

      {/* Message */}
      <div style={{
        fontSize:     '0.62rem',
        color:        isError ? '#c05840' : '#7a9a68',
        fontStyle:    'italic',
        overflow:     'hidden',
        textOverflow: 'ellipsis',
        whiteSpace:   'nowrap',
      }}>
        {displayMsg}
      </div>
    </div>
  );
}

export default FetchProgressBar;