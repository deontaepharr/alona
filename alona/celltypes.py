""" alona

 Description: An analysis pipeline for scRNA-seq data.

 How to use: https://github.com/oscar-franzen/alona/

 Contact: Oscar Franzen <p.oscar.franzen@gmail.com> """

import os
import sys
import subprocess
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact
from sklearn.preprocessing import scale
from sklearn.preprocessing import MinMaxScaler
import matplotlib.pyplot as plt
import matplotlib.transforms as transforms
import matplotlib.patches as patches
import seaborn as sb

from .clustering import AlonaClustering
from .constants import (OUTPUT, GENOME, MARKERS)
from .log import (log_info, log_debug, log_error)
from .utils import (get_alona_dir, get_time, uniqueColors)
from .stats import p_adjust_bh


class AlonaCellTypePred(AlonaClustering):
    """
    Cell type prediction methods.
    """

    def __init__(self):
        self.markers = None
        self.marker_freq = None
        self.res_pred = None
        super().__init__()

    def median_exp(self, data_norm):
        """ Represent each cluster with median gene expression. """
        log_debug('median_exp() Computing median expression per cluster')
        clust = self.leiden_cl
        data = data_norm
        fn = self.get_wd() + OUTPUT['FILENAME_MEDIAN_EXP']
        ret = data.groupby(clust, axis=1).aggregate(np.median)
        ret = ret.iloc[:, ret.columns.isin(self.clusters_targets)]
        if type(self.anno) == pd.core.frame.DataFrame:
            ret = pd.concat([self.anno['desc'], ret], axis=1)
        ret.to_csv(fn, header=True, sep='\t')
        log_debug('median_exp() finished')
        return ret

    def mean_exp(self):
        """ Represent each cluster with mean gene expression. """
        log_debug('mean_exp() Computing mean expression per cluster')
        clust = self.leiden_cl
        data = self.data_norm
        fn = self.get_wd() + OUTPUT['FILENAME_MEAN_EXP']
        ret = data.groupby(clust, axis=1).aggregate(np.mean)
        ret = ret.iloc[:, ret.columns.isin(self.clusters_targets)]
        #self.mean_expr = ret
        if type(self.anno) == pd.core.frame.DataFrame:
            ret = pd.concat([self.anno['desc'], ret], axis=1)
        ret.to_csv(fn, header=True, sep='\t')
        log_debug('mean_exp() finished')

    def get_gene_symbols(self, data_norm):
        if self.params['species'] == 'mouse':
            f = GENOME['SYMBOLS_MOUSE']
        else:
            f = GENOME['SYMBOLS_HUMAN']
        refs = pd.read_csv(get_alona_dir()+f, sep='\t', header=None)
        refs.index = refs.iloc[:, 0]
        t = refs.iloc[:, 1].value_counts() == 1
        t = t[t]
        refs = refs[refs.iloc[:, 1].isin(t.index)]
        data_norm.index = data_norm.index.str.upper()
        if not data_norm.index.str.match('ENSMU').any() and \
           not data_norm.index.str.match('ENSG').any():
            return data_norm
        if data_norm.index.str.match(r'^ENS(G|MUS(G){0,1})\d+$').any(): #refactored and works
            data_norm = data_norm.iloc[data_norm.index.isin(refs.index), :]
            refs = refs.iloc[refs.index.isin(data_norm.index), :]
            refs = refs.reindex(data_norm.index)
            data_norm.index = refs.iloc[:, 1]
        else:
            pass
            # median_expr.index.str.extract('^(.+)_.+')[0].str.upper()
        return data_norm

    def CTA_RANK_F(self, marker_plot=False):
        """ Cell Type Activity and Rank-based annotation with a
        one-sided Fisher's Exact test """
        if not self.params['species'] in ['mouse', 'human']:
            log_info('"--species other", skipping cell type prediction')
            return
        log_debug('CTA_RANK_F() starting')
        #import joblib
        #joblib.dump(self, 'q.jl')
        # sys.exit()
        data_norm = self.data_norm.copy()
        data_norm = self.get_gene_symbols(data_norm)
        median_expr = self.median_exp(data_norm)
        markers = self.markers
        freq = self.marker_freq
        input_symbols = median_expr.index
        median_expr.index = input_symbols
        # (1) centering is done by subtracting the column means
        # (2) scaling is done by dividing the (centered) by their
        # standard deviations
        median_expr_Z = pd.DataFrame(
            scale(median_expr, with_mean=True, axis=0))
        median_expr_Z.index = median_expr.index
        median_expr_Z.columns = median_expr.columns
        # reference symbols
        #fn = get_alona_dir() + GENOME['MOUSE_GENE_SYMBOLS']
        #mgs = pd.read_csv(fn, header=None)
        #mgs = mgs[0].str.upper()
        #markers = markers[markers[markers.columns[0]].isin(mgs)]
        dd = defaultdict(list)
        for item in markers.groupby('cell type'):
            dd[item[0]] = set(item[1][item[1].columns[0]])
        # Following this reasoning:
        # Down-weighting overlapping genes improves gene set analysis
        # Tarca AL, Draghici S, Bhatti G, Romero R
        # BMC Bioinformatics 2012 13:136
        #s = mgs.unique()
        #s_freqs = marker_freq[marker_freq.index.isin(s)]
        weights = 1+np.sqrt(((max(freq)-freq)/(max(freq)-min(freq))))

        def _guess_cell_type(x):
            rr = median_expr.loc[:, median_expr.columns ==
                                 x.name].values.flatten()
            # genes expressed in this cell cluster
            genes_exp = set(x.index[rr > 0])
            # genes _not_ expressed in this cell cluster
            genes_not_exp = set(x.index[rr == 0])
            res = list()
            for ct in dd:
                s = dd[ct]
                x_ss = x[x.index.isin(s)]
                if len(x_ss) == 0:
                    continue
                gene_weights = weights[weights.index.isin(x_ss.index)]
                gene_weights = pd.Series(gene_weights, x_ss.index)
                activity_score = sum(x_ss * gene_weights)/len(x_ss)**0.3
                # how many expressed genesets are found in the geneset?
                ct_exp = len(genes_exp & s)
                # how many _non_ expressed genes are found in the geneset?
                ct_non_exp = len(genes_not_exp & s)
                # how many expressed genes are NOT found in the geneset?
                ct_exp_not_found = len(genes_exp-s)
                # how many _non_ expressed genes are NOT found in the geneset?
                not_exp_not_found_in_geneset = len(genes_not_exp-s)
                # one sided fisher
                contigency_tbl = [[ct_exp, ct_non_exp],
                                  [ct_exp_not_found,
                                  not_exp_not_found_in_geneset]]
                odds_ratio, pval = fisher_exact(
                    contigency_tbl, alternative='greater')
                markers_found = ','.join(list(genes_exp & s))
                if markers_found == '':
                    markers_found = 'NA'
                res.append({'activity_score': activity_score,
                            'ct': ct,
                            'pvalue': pval,
                            'markers': markers_found})
            res = sorted(res, key=lambda k: k['activity_score'], reverse=True)
            return res
        ret = median_expr_Z.apply(func=_guess_cell_type, axis=0)
        # restructure
        bucket = []
        for i, k in enumerate(ret):
            _df = pd.DataFrame(ret[k].to_list()) #refactored
            _df['cluster'] = [i]*len(ret[k]) #refactored
            cols = _df.columns.tolist()
            _df = _df[cols[-1:]+cols[:-1]]
            bucket.append(_df)
        final_tbl = pd.concat(bucket)
        padj = p_adjust_bh(final_tbl['pvalue'])
        final_tbl['padj_BH'] = padj
        final_tbl.columns = ['cluster',
                             'activity score',
                             'cell type',
                             'p-value',
                             'markers',
                             'adjusted p-value BH']
        fn = self.get_wd() + OUTPUT['FILENAME_CTA_RANK_F']
        final_tbl.to_csv(fn, sep='\t', index=False)
        # Save the best scoring for each cluster
        self.res_pred = final_tbl.groupby('cluster').nth(0)
        _a = self.res_pred['adjusted p-value BH'] > 0.10
        self.res_pred.loc[_a, 'cell type'] = 'Unknown'
        fn = self.get_wd() + OUTPUT['FILENAME_CTA_RANK_F_BEST']
        self.res_pred.to_csv(fn, sep='\t', index=True)
        if marker_plot:
            log_debug('Generating heatmap...')
            # additional cell types
            add_ct = self.params['add_celltypes']
            # sort on all the cell types that the gene occurs in
            zx = self.res_pred['cell type'] != 'Unknown'
            if not np.any(zx):
                log_debug('Not generating marker plot, because all \
cell types were "Unknown".')
                return
            ct_targets = self.res_pred[zx]['cell type'].unique()
            zx = self.res_pred['cell type'].isin(ct_targets)
            df = self.res_pred[zx][['cell type', 'markers']]
            if add_ct:
                for item in add_ct.upper().split(','):
                    if not np.any(df['cell type'] == item):
                        zx = self.markers['cell type'].str.upper() == item
                        key = 'official gene symbol'
                        ct_mark = self.markers[zx][key].str.cat(sep=',')
                        zx = self.markers['cell type'].str.upper() == item
                        l = self.markers[zx]['cell type'].unique()[0]
                        zx = pd.Series([l, ct_mark], index=[
                                       'cell type', 'markers'])
                        df = df.append(zx, ignore_index=True)
                        ct_targets = np.append(ct_targets, l)
            dff = df['markers'].str.split(',', expand=True)
            dff['cell type'] = df['cell type'].values
            dff = dff.melt(id_vars='cell type')
            dff = dff[dff['value'].values != None]
            dff = dff[['cell type', 'value']]
            dff = dff.drop_duplicates()
            ct_color = uniqueColors(len(ct_targets))
            gene = []
            celltypes = []
            for item in dff.groupby(['value']):
                gene.append(item[0])
                celltypes.append(','.join(sorted(item[1]['cell type'].values)))
            dff = pd.DataFrame({'gene': gene, 'cell types': celltypes})
            dff = dff.sort_values('cell types')
            dff.index = np.arange(1, dff.shape[0]+1)
            target_genes = dff.gene
            symbs = data_norm.index
            data_slice = data_norm.loc[symbs.isin(target_genes)]
            cell_ids = pd.DataFrame({'ids': data_slice.columns.values,
                                     'cluster': self.leiden_cl})
            cell_ids = cell_ids[cell_ids['cluster'].isin(
                self.clusters_targets)]
            cell_ids = cell_ids.sort_values(by='cluster')
            data_slice = data_slice.loc[:, data_slice.columns.isin(
                cell_ids['ids'].values)]
            data_slice = data_slice.reindex(cell_ids['ids'], axis=1)
            data_slice = data_slice.reindex(dff['gene'], axis=0)
            plt.clf()
            fig_size_y = round(data_slice.shape[0]/8)  # 8 genes per inch
            fig, ax = plt.subplots(
                nrows=1, ncols=1, figsize=(15, fig_size_y))  # xy
            ax = sb.heatmap(data_slice,
                            linewidth=0,
                            # controls size of the colorbar
                            cbar_kws={"shrink": 0.5})
            ax.get_xaxis().set_visible(False)
            ax.get_yaxis().set_visible(False)
            cbar = ax.collections[0].colorbar.ax
            cbar.set_position([0.80, 0.6, 0.19, 0.19])
            # setting ylim is needed due to matplotlib/seaborn bug
            # shuld be highest to lowest or data will flip
            ax.set_ylim([data_slice.shape[0], 0])
            ax.set_xlim([0, data_slice.shape[1]])
            # x coordinate is axes and y coordinate is data
            trans = transforms.blended_transform_factory(
                ax.transAxes, ax.transData)
            # add gene labels
            y_data_coord = 1  # data coordinates starts at 1
            for gene in data_slice.index:
                ax.text(x=-0.012-0.012*len(ct_targets), y=y_data_coord, s=gene,
                        horizontalalignment='right', clip_on=False, size=7,
                        transform=trans)
                y_data_coord += 1
            ax.collections[0].colorbar.ax.tick_params(labelsize=6)
            ax.collections[0].colorbar.set_label(
                'gene expression (log2 scale)', size=6)
            grid = np.array(sorted(ct_targets))
            # cell type labels
            offset = -0.012-0.012*len(ct_targets)+0.006
            for idx, ct in enumerate(grid):
                ax.text(offset+idx*0.011, 0-0.50, ct, size=6,
                        rotation=90, clip_on=False, transform=trans,
                        color=ct_color[idx])
            index = 0
            for idx, d in dff.iterrows():
                z = d[1].split(',')
                for p in z:
                    i = np.where(grid == p)[0][0]
                    rect = patches.Rectangle((offset+i*0.011,
                                             index+0.3), 0.005, 0.6,
                                             linewidth=2,
                                             facecolor=ct_color[i],
                                             clip_on=False,
                                             transform=trans)
                    ax.add_patch(rect)
                index += 1
            # add cluster indicators
            xmin = 0
            xmax = 0
            for cl in self.clusters_targets:
                cell_count = np.sum(np.array(self.leiden_cl) == cl)
                xmax += cell_count
                col = self.cluster_colors[cl]
                # y, xmin, xmax
                ax.hlines(-0.5, xmin, xmax, color=col, clip_on=False, lw=4)
                # cluster index
                ax.text(x=xmin, y=-1.2, s=cl, size=5)
                xmin += cell_count
                # ax.get_xlim()[1]
            if self.params['timestamp']:
                plt.figtext(0.05, 0.05, get_time(), size=4)
            fn = self.get_wd() + OUTPUT['FILENAME_MARKER_HEATMAP']
            plt.savefig(fn, bbox_inches='tight')
            #import joblib
            # joblib.dump(self,'q.jl')
        log_debug('CTA_RANK_F() finished')

    def load_markers(self):
        """ Load gene to cell type markers. """
        log_debug('Loading markers...')
        ma = pd.read_csv(get_alona_dir() + MARKERS['PANGLAODB'], sep='\t')
        if self.params['species'] == 'mouse':
            s = 'Mm'
        else:
            s = 'Hs'
        ma = ma[ma.species.str.find(s) > -1]
        ui = ma.iloc[:, ma.columns == 'ubiquitousness index']
        ma = ma[np.array(ui).flatten() < 0.05]
        log_debug('Markers loaded')
        # marker frequency across the cell types
        ma_ss = ma.iloc[:, ma.columns.isin(
            ['official gene symbol', 'cell type'])]
        self.marker_freq = ma_ss[ma_ss.columns[0]].value_counts()
        self.markers = ma_ss
