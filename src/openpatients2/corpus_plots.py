"""Scalar-count histograms; no persisted token IDs or interactive network assets."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .data import write_json


def histogram(values, *, logarithmic=False):
    values=np.asarray(list(values),dtype=float)
    if not len(values) or not np.all(np.isfinite(values)) or np.any(values<0):
        raise ValueError('Histogram needs nonnegative finite counts')
    if logarithmic and np.any(values<=0):
        raise ValueError('Log token histogram needs positive counts')
    working=np.log10(values) if logarithmic else values
    bins=min(64,max(3,len(np.histogram_bin_edges(working,bins='fd'))-1))
    counts,edges=np.histogram(working,bins=bins)
    if logarithmic: edges=10**edges
    return {'counts':counts.tolist(),'edges':edges.tolist(),'n':len(values),
            'axis':'log10' if logarithmic else 'linear',
            'bin_rule':'Freedman-Diaconis, bounded to 3..64 bins',
            'markers':{name:float(np.percentile(values,p)) for name,p in [('median',50),('p75',75),('p95',95)]}}


def write_histograms(rows, output, *, label='Purposive licensed PMC sample', metrics=None):
    output=Path(output)
    metrics=metrics or {'prose_tokens':'Cleaned article prose', 'roster_prompt_tokens':'Full roster prompt'}
    result={'label':label,'articles':len(rows),'population_representative':False,
            'token_ids_written':False,'distributions':{},'files':['token-histograms.json']}
    for key in metrics:
        values=[row[key] for row in rows]
        result['distributions'][key]={'linear':histogram(values), 'log':histogram(values,logarithmic=True)}
    write_json(output/'token-histograms.json',result)
    try:
        # Headless HPC safe, with cache owned by the output directory.
        os.environ.setdefault('MPLCONFIGDIR',str(output/'.plot-cache'))
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        result.update(status='data_only',notice='Install the locked plots extra to render PNG/SVG; histogram data is complete.')
        write_json(output/'token-histograms.json',result)
        return result
    fig,axes=plt.subplots(len(metrics),2,figsize=(12,3.6*len(metrics)),layout='constrained',squeeze=False)
    colors={'median':'#23886b','p75':'#be7910','p95':'#b3414b'}
    for row_index,(key,title) in enumerate(metrics.items()):
        for col,axis_name in enumerate(('linear','log')):
            axis=axes[row_index,col]; data=result['distributions'][key][axis_name]
            axis.stairs(data['counts'],data['edges'],fill=True,color='#527bb4',alpha=.75)
            if axis_name=='log': axis.set_xscale('log')
            for marker,value in data['markers'].items():
                axis.axvline(value,color=colors[marker],linestyle='--',linewidth=1.3,
                             label=f'{marker.upper()}: {value:,.0f}')
            axis.set_title(title+(' (log scale)' if axis_name=='log' else ''))
            axis.set_xlabel('Glimmer tokens');axis.set_ylabel('Articles')
            axis.legend(fontsize=8);axis.grid(axis='y',alpha=.2)
    fig.suptitle(f'{label} — {len(rows)} articles\nDescriptive sample; not a population estimate',fontsize=13)
    for suffix in ('png','svg'): fig.savefig(output/f'token-histograms.{suffix}',dpi=160)
    plt.close(fig)
    result.update(status='rendered',files=['token-histograms.json','token-histograms.png','token-histograms.svg'])
    write_json(output/'token-histograms.json',result)
    return result
