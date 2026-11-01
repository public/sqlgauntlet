#!/usr/bin/env python3
"""mkpeter.py [frames]: the second workload, Peter Gostev's DOOMQL (src/petergpt-doomql, MIT, written for SQLite), driven
headless like its host/doomql.py: per frame the transport sample (clock, raw key bytes) is written, upstream's
sql/tick.sql runs, then the render query. No port is needed: upstream's SQL runs unchanged.
  work/peter/inputs.txt   one line of SQL per frame: game_clock.sample_us (30 fps) and the held key's raw bytes
  work/peter/frame.sql    upstream's RENDER_QUERY (host/doomql.py): the frame as ANSI scanlines
  work/peter/pixels.sql   the raster itself, SELECT x, y, r, g, b FROM frame_pixels (for PNGs)"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
B = os.environ.get('DOOMQL_BUILD', os.path.join(HERE, '..', '..', 'build', 'workloads', 'doomql'))
W = os.path.join(B, 'work', 'peter')

def lcg(seed):
    while True:
        seed = (seed * 6364136223846793005 + 1442695040888963407) & (2**64 - 1)
        yield seed >> 33

def main():
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    os.makedirs(W, exist_ok=True)
    r = lcg(11)
    keys = ['77'] * 8 + ['6A'] * 3 + ['6C'] * 3 + ['20'] * 3 + ['61', '64', '73', '65'] + [''] * 2   # w, j, l, space, a, d, s, e
    lines, held, left = [], '', 0
    for f in range(frames):
        us = 1_000_000 + f * 33_333
        if left == 0: held, left = keys[next(r) % len(keys)], 3 + next(r) % 10   # hold a key for 3-12 frames
        left -= 1
        ev = f" INSERT INTO input_events(captured_us, raw_code) VALUES ({us}, X'{held}');" if held else ''
        lines.append(f"UPDATE game_clock SET sample_us = {us} WHERE id = 1;{ev}")
    open(os.path.join(W, 'inputs.txt'), 'w').write('\n'.join(lines) + '\n')
    open(os.path.join(W, 'frame.sql'), 'w').write('SELECT scanline FROM render_frame ORDER BY y;\n')
    open(os.path.join(W, 'pixels.sql'), 'w').write('SELECT x, y, r, g, b FROM frame_pixels ORDER BY y, x;\n')
    print(f'mkpeter: work/peter ({frames} frames)')

if __name__ == '__main__':
    main()
