# -*- coding: utf-8 -*-
"""
Created on 8/8/18

based on pip_trains_unique.py
"""

import os
from datetime import datetime
from hashlib import md5

import expyfun.stimuli as stim
import numpy as np
import scipy.signal as sig
from expyfun.io import read_hdf5, write_hdf5


def generate_stimuli(rate=40, do_pips=True, n_minutes=2, save_dir='stimuli'):
    fs = 48000.
    dur_trial = 1.  # duration of stimulus epoch
    if do_pips:
        f_band = [500, 1000, 2000, 4000, 8000]
    else:
        f_band = [0]

    n_per = 5  # number of periods in envelope
    click_dur = 100e-6
    click_len = int(np.round(fs * click_dur))
    print('Click dur is %i microseconds.' % (click_len * 1e6 / fs))
    f_band = np.array(f_band, dtype=float)

    n_band = len(f_band)
    dur_band = n_per / f_band
    dur_band[f_band == 0] = click_dur
    len_band = np.round(dur_band * fs).astype(int)
    len_band += 1 - len_band % 2
    len_band_max = len_band.max()
    invert_sign = True  # alternate polarity
    interleave_flips = True
    if interleave_flips:
        n_minutes /= 2
    n_tokens = int(np.ceil(60 / dur_trial * n_minutes))

    if do_pips:
        pips = [np.abs(np.blackman(n)) *
                np.cos((np.arange(n) - (n - 1) / 2.) / float(fs) * f * 2 * np.pi)
                for n, f in zip(len_band, f_band)]
        pips = [0.01 / np.sqrt(0.5) * p for p in pips]
        for i in range(len(pips)):
            pips[i] = np.pad(pips[i], (0, len_band_max - len_band[i]), 'constant')
    else:  # doing clicks
        pips = [np.ones(click_len) * 0.01 / np.sqrt(0.5)]
        len_band_max = click_len
    pips = np.array(pips)

    # construct the pip sequences
    rand = np.random.RandomState(f_band.astype(int))
    print(np.round(f_band).astype(int))

    # make a trial
    len_trial = int(np.round(dur_trial * fs))

    if np.isscalar(rate):
        rate *= np.ones(f_band.shape)

    print('RATE: {}'.format(rate))
    n_ch = 2  # 1 or 2

    x_pulse_all = []
    x_all = []
    for _ in range(n_tokens):
        print('Token %3i of %i' % (_ + 1, n_tokens))
        x_pulse = np.zeros((n_ch, n_band, len_trial))
        x = np.zeros((n_ch, n_band, len_trial))

        n_pips = 2 * (np.round(rate * (dur_trial -
                                       dur_band.max())).astype(int) // 2)
        pip_inds = [np.array([rand.permutation(
                len_trial - int(fs * dur_band.max()))[:n] +
                    int((dur_band.max() - d) / 2. * fs) for _ in range(n_ch)])
                    for n, d in zip(n_pips, dur_band)]

        for bi, inds in enumerate(pip_inds):
            for ch in range(n_ch):
                x_pulse[ch, bi, inds[ch]] = -1  # -1 means rarefaction by default
                if invert_sign:
                    ind_inds = rand.permutation(
                        n_pips[bi])[:n_pips[bi] // 2]
                    x_pulse[ch, bi, inds[ch, ind_inds]] *= -1
                x[ch, bi] = sig.fftconvolve(x_pulse[ch, bi],
                                            pips[bi])[..., :len_trial]

        x_pulse_all += [x_pulse]
        x_all += [x]
    x_pulse_all = np.asarray(x_pulse_all)
    x_all = np.asarray(x_all)

    if interleave_flips:
        x_all = (
                x_all[np.arange(n_tokens * 2) // 2] *
                (-1) ** np.arange(n_tokens * 2)[:, np.newaxis,
                                                np.newaxis, np.newaxis])
        x_pulse_all = (
                x_pulse_all[np.arange(n_tokens * 2) // 2] *
                (-1) ** np.arange(n_tokens * 2)[:, np.newaxis,
                                                np.newaxis, np.newaxis])

    os.makedirs(save_dir, exist_ok=True)
    if do_pips:
        fn = os.path.join(save_dir, 'pips_rate%i.hdf5' % rate[0])
    else:
        fn = os.path.join(save_dir, 'clicks_rate%i.hdf5' % rate[0])
    
    # check if file exists and if so, append timestamp to avoid overwriting
    if os.path.exists(fn):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base, ext = os.path.splitext(fn)
        fn = f"{base}_{timestamp}{ext}"
        print(f"File already exists. Saving as {fn} instead.")
    
    # add a hash of the stimulus to the dict for reproducibility
    stim_hash = md5(x_all.tobytes()).hexdigest()

    write_hdf5(fn, dict(
        x=x_all,
        x_pulse=x_pulse_all,
        n_tokens=n_tokens * (1 + interleave_flips),
        rate=rate,
        invert_sign=invert_sign,
        fs=fs,
        f_band=f_band,
        pips=pips,
        dur_band=dur_band,
        interleave_flips=interleave_flips,
        stim_hash=stim_hash
        ), overwrite=False)
    print('Done. Saved to %s' % fn)
    return fn

if __name__ == '__main__':
    generate_stimuli()
