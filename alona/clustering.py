""" This file contains clustering methods used by alona.

 In general, it flows like this:

    1. identify highly variable genes (HVG), retrieve N genes
    2. perform PCA on the HVG, retrieve N components 3. adjust PCAs by
    weight 4. compute KNN 5. compute SNN from KNN, prune SNN graph
    6. identify communities with leiden algo 7. run t-SNE or UMAP on
    the PCAs

 How to use alona: https://github.com/oscar-franzen/alona/

 Contact: Oscar Franzen <p.oscar.franzen@gmail.com> """

import os
import re
import sys
import joblib

import numpy as np
import pandas as pd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import seaborn as sb
import sklearn.manifold
from sklearn.decomposition import PCA as sklearn_pca
from sklearn.preprocessing import scale
from sklearn.neighbors import NearestNeighbors
from scipy.sparse import coo_matrix
import scipy.linalg
import umap
import leidenalg
import igraph as ig

import alona.irlbpy
from .alonabase import AlonaBase
from .cell import AlonaCell
from .hvg import AlonaHighlyVariableGenes

from .log import (log_info, log_debug, log_error, log_warning)
from .constants import OUTPUT
from .utils import (get_alona_dir, uniqueColors, get_time)


class AlonaClustering(AlonaCell):
    """
    Clustering class.
    """

    def __init__(self):
        self.hvg = None
        self.pca_components = None
        self.embeddings = None  # pd.DataFrame
        self.nn_idx = None
        self.snn_graph = None
        self.leiden_cl = None
        self.cluster_colors = []
        super().__init__()

        # Changing the font will remove '-' in tick labels
        #matplotlib.rcParams['font.sans-serif'] = 'Arial'
        #matplotlib.rcParams['font.family'] = 'sans-serif'

    def find_variable_genes(self):
        log_debug('Entering find_variable_genes()')
        v = self.params['hvg_method']
        hvg_finder = AlonaHighlyVariableGenes(hvg_method=v,
                                              hvg_n=self.params['hvg_n'],
                                              data_norm=self.data_norm,
                                              data_ERCC=self.data_ERCC)

        self.hvg = hvg_finder.find()
        if type(self.anno) == pd.core.frame.DataFrame:
            w = pd.DataFrame(self.hvg, index=self.hvg, columns=['gene'])
            w = w.merge(self.anno)
        else:
            w = pd.DataFrame(self.hvg)
        fn = self.get_wd() + OUTPUT['FILENAME_HVG']
        w.to_csv(fn, header=False, index=False, sep='\t')
        log_debug('Finished find_variable_genes()')

    def PCA(self, out_path):
        """ Calculate principal components.  Default is to approximate
        PCA using truncated singular value decomposition (IRLBA).

        The augmented implicitly restarted Lanczos bidiagonalization
        algorithm (IRLBA) finds a few approximate largest singular
        values and corresponding singular vectors using a method of
        Baglama and Reichel.

        A fast and memory-efficient way to compute a partial SVD,
        principal components, and some specialized partial eigenvalue
        decompositions.

        Reference: Baglama, James, and Lothar Reichel. “Augmented
        implicitly restarted Lanczos bidiagonalization methods.” SIAM
        Journal on Scientific Computing 27.1 (2005): 19-42.

        Some useful notes about the R implementation:
        http://bwlewis.github.io/irlba/ """
        log_debug('Running PCA...')

        n_comp = self.params['pca_n']
        index_v = self.data_norm.index.isin(self.hvg)
        sliced = self.data_norm[index_v]
        seed = self.params['seed']
        if self.params['pca'] == 'irlb':
            lanc = alona.irlbpy.lanczos(
                sliced, nval=n_comp, maxit=1000, seed=seed)
            # weighing by var
            self.pca_components = np.dot(lanc.V, np.diag(lanc.s))
            self.pca_components = pd.DataFrame(
                self.pca_components, index=sliced.columns)
        elif self.params['pca'] == 'regular':
            sliced = sliced.transpose()
            x = scale(sliced, with_mean=True, with_std=False)
            s = scipy.linalg.svd(x)
            v = s[2].transpose()
            d = s[1]
            s_d = d/np.sqrt(x.shape[0]-1)
            retx = x.dot(v)
            retx = retx[:, 0:n_comp]
            self.pca_components = retx
            self.pca_components = pd.DataFrame(
                self.pca_components, index=sliced.index)
        self.pca_components.to_csv(path_or_buf=out_path, sep=',', header=None)
        log_debug('Finished PCA')

    def embedding(self, out_path):
        """ Calls t-SNE or UMAP """
        method = self.params['embedding']
        if method == 'tSNE':
            self.tSNE(out_path)
        elif method == 'UMAP':
            self.UMAP(out_path)
        else:
            log_error('Method not implemented.')

    def UMAP(self, out_path):
        """ Projects data to a two dimensional space using the UMAP
        algorithm.

        References: McInnes L, Healy J, Melville J, arxiv, 2018

        https://arxiv.org/abs/1802.03426
        https://github.com/lmcinnes/umap
        https://umap-learn.readthedocs.io/en/latest/ """
        log_debug('Entering UMAP()')
        seed = self.params['seed']
        reducer = umap.UMAP(random_state=seed)
        self.embeddings = reducer.fit_transform(self.pca_components)
        self.embeddings = pd.DataFrame(self.embeddings,
                                       index=self.pca_components.index,
                                       columns=[1, 2])
        self.embeddings.to_csv(path_or_buf=out_path, sep=',', header=None)
        log_debug('Exiting UMAP()')

    def tSNE(self, out_path):
        """ Projects data to a two dimensional space using the tSNE
        algorithm.

        van der Maaten, L.J.P.; Hinton, G.E. Visualizing
        High-Dimensional Data Using t-SNE. Journal of Machine Learning
        Research 9:2579-2605, 2008.  """
        log_debug('Running t-SNE...')
        seed = self.params['seed']
        perplexity = self.params['perplexity']
        tsne = sklearn.manifold.TSNE(n_components=2,
                                     n_iter=2000,
                                     perplexity=perplexity,
                                     random_state=seed)
        self.embeddings = tsne.fit_transform(self.pca_components)
        self.embeddings = pd.DataFrame(self.embeddings,
                                       index=self.pca_components.index,
                                       columns=[1, 2])
        self.embeddings.to_csv(path_or_buf=out_path, sep=',', header=None)
        log_debug('Finished t-SNE')

    def knn(self, inp_k, filename=''):
        """ Nearest Neighbour Search. Finds the k number of near
        neighbours for each cell.  """
        log_debug('Performing Nearest Neighbour Search')
        k = inp_k
        nbrs = NearestNeighbors(n_neighbors=k, algorithm='ball_tree')
        nbrs.fit(self.pca_components)
        indices = nbrs.kneighbors(self.pca_components)[1]
        self.nn_idx = indices+1
        log_debug('Finished NNS')

    def snn(self, k, prune_snn):
        """ Computes Shared Nearest Neighbor (SNN) Graph Link weights
        are number of shared nearest neighbors, so we need to get the
        sum of SNN similarities over all KNNs, which is done with a
        matrix operation.  See:
        http://mlwiki.org/index.php/SNN_Clustering """
        log_debug('Computing SNN graph...')
        snn_path = self.get_wd() + OUTPUT['FILENAME_SNN_GRAPH']
        if os.path.exists(snn_path):
            log_debug('Loading SNN from file...')
            self.snn_graph = pd.read_csv(snn_path, header=None)
            return
        k_param = k
        # create sparse matrix from tuples
        melted = pd.DataFrame(self.nn_idx).melt(id_vars=[0])[[0, 'value']]
        rows = np.array(melted[melted.columns[0]])
        cols = np.array(melted[melted.columns[1]])
        d = [1]*len(rows)
        rows = np.array(list(melted[melted.columns[0]].values) +
                        list(range(1, self.nn_idx.shape[0]+1)))
        cols = np.array(list(melted[melted.columns[1]]) +
                        list(list(range(1, self.nn_idx.shape[0]+1))))
        d = [1]*len(rows)
        knn_sparse = coo_matrix((d, (rows-1, cols-1)),
                                shape=(self.nn_idx.shape[0],
                                       self.nn_idx.shape[0]))
        snn_sparse = knn_sparse*knn_sparse.transpose()
        # prune using same logic as FindClusters in Seurat
        #aa = snn_sparse.nonzero()
        cx = coo_matrix(snn_sparse)
        node1 = []
        node2 = []
        pruned_count = 0
        for i, j, v in zip(cx.row, cx.col, cx.data):
            item = (i, j, v)
            strength = v/(k+(k-v))

            if strength > prune_snn:
                node1.append(i)
                node2.append(j)
            else:
                pruned_count += 1
        perc_pruned = (pruned_count/len(cx.row))*100
        s = '{:,}'.format(pruned_count)
        log_debug('%.2f%% (n=%s) of links pruned' % (perc_pruned, s))
        if perc_pruned > 80:
            log_warning('more than 80% of the edges were pruned')
        df = pd.DataFrame({'source_node': node1, 'target_node': node2})
        df.to_csv(snn_path, header=None, index=None)
        self.snn_graph = df
        log_debug('Done computing SNN.')

    def leiden_prep(self):
        """ Post-clustering stuff. """
        fn = self.get_wd() + OUTPUT['FILENAME_CLUSTERS_LEIDEN']
        idx = self.data_norm.columns
        pd.DataFrame(self.leiden_cl, index=idx).to_csv(
            fn, header=False, index=True)
        cl, counts = np.unique(self.leiden_cl, return_counts=True)
        ignore_clusters = self.params['ignore_small_clusters']
        self.n_clusters = np.sum(counts > ignore_clusters)
        n = len(set(self.leiden_cl))
        log_info('there are %s cell clusters (n=%s are OK)' %
                 (n, self.n_clusters))
        self.clusters_targets = cl[counts > ignore_clusters]
        log_debug(('cluster', 'cells'))
        for item in zip(cl, counts):
            log_debug(item)
        if not self.cluster_colors:
            # generate some unique colors
            self.cluster_colors = uniqueColors(len(self.clusters_targets))

    def leiden(self):
        """ Cluster the SNN graph using the Leiden algorithm.

        https://github.com/vtraag/leidenalg

        From Louvain to Leiden: guaranteeing well-connected
        communities Traag V, Waltman L, van Eck NJ
        https://arxiv.org/abs/1810.08473 """
        log_debug('Running leiden clustering...')
        res = self.params['leiden_res']
        seed = self.params['seed']
        # construct the graph object
        nn = set(self.snn_graph[self.snn_graph.columns[0]])
        g = ig.Graph()
        g.add_vertices(len(nn))
        g.vs['name'] = list(range(1, len(nn)+1))
        ll = []
        for i in self.snn_graph.itertuples(index=False):
            ll.append(tuple(i))
        g.add_edges(ll)
        if self.params == 'ModularityVertexPartition':
            part = leidenalg.ModularityVertexPartition
        else:
            part = leidenalg.RBERVertexPartition
        cl = leidenalg.find_partition(g,
                                      part,
                                      n_iterations=10,
                                      resolution_parameter=res,
                                      seed=seed)
        self.leiden_cl = cl.membership
        self.leiden_prep()
        log_debug('Leiden has finished.')

    def cluster(self):
        """ Clusters or loads a pre-made clustering. """
        if type(self.preclust) == pd.core.frame.DataFrame:
            t = self.preclust.cell.isin(self.data_norm.columns)
            self.preclust = self.preclust.loc[t, :]
            if self.data_norm.shape[1] != self.preclust.shape[0]:
                log_error('Number of cells mismatch (data_norm and preclust)')
            self.data_norm = self.data_norm.reindex(
                self.preclust['cell'], axis=1)
            self.leiden_cl = list(self.preclust['cluster'])
            self.leiden_prep()
        else:
            k = self.params['nn_k']
            fn_knn_map = self.get_wd() + OUTPUT['FILENAME_KNN_map']
            self.knn(k, filename=fn_knn_map)
            self.snn(k, self.params['prune_snn'])
            self.leiden()

    def cell_scatter_plot(self, title=''):
        """ Generates a tSNE scatter plot with colored clusters. """
        log_debug('Generating scatter plot...')
        dark_bg = self.params['dark_bg']
        method = self.params['embedding']
        ignore_clusters = self.params['ignore_small_clusters']
        highlight_specific_cells = self.params['highlight_specific_cells']
        if highlight_specific_cells:
            highlight_specific_cells = re.sub(
                ' ', '', highlight_specific_cells).split(',')
        else:
            highlight_specific_cells = []
        if dark_bg:
            # Don't remove this block.
            # For some reason this block is needed for --dark_bg to function.
            plt.clf()
            fig = plt.figure(num=None, figsize=(5, 5))
            fig.add_subplot(111)
            plt.style.use('dark_background')
            plt.scatter(1, 1, s=1)
            plt.savefig('/tmp/_.pdf', bbox_inches='tight')
            plt.close()
        plt.clf()
        fig = plt.figure()  # num=None, figsize=(5, 5)
        grid = plt.GridSpec(nrows=1, ncols=5, hspace=0.2, wspace=0.2)
        # python note, A:B (A=0 indexed, B=1 indexed)
        main_ax = plt.subplot(grid[0, 0:4])
        leg1 = plt.subplot(grid[0, -1])  # 3 is 0 indexed
        leg1.set_xlim(0, 1)
        leg1.set_ylim(0, 1)
        leg1.axis('off')
        if dark_bg:
            log_debug('using dark background (--dark_bg is set)')
            plt.style.use('dark_background')
        cell_count = self.embeddings.shape[0]
        if cell_count > 1000:
            marker_size = 0.8
        else:
            marker_size = 3
        offset = 0
        ignored_count = 0
        special_cells = []
        # plot the first legend
        for i in range(len(self.clusters_targets)):
            idx = np.array(self.leiden_cl) == i
            e = self.embeddings[idx]
            col = self.cluster_colors[i]
            if np.any(e.index.isin(highlight_specific_cells)):
                special_cell = e[e.index.isin(highlight_specific_cells)]
                special_cells.append({'np': special_cell,
                                      'col': col,
                                      'cell_id': special_cell.index.values})
                e = e.drop(special_cell.index, axis=0)
            x = e[1].values
            y = e[2].values
            if e.shape[0] <= ignore_clusters:
                ignored_count += 1
                continue
            main_ax.scatter(x, y, s=marker_size, color=col,
                            label=self.clusters_targets[i])
            lab = i
            rect = mpatches.Rectangle((0.05, 1-0.03*i - 0.05),
                                      width=0.20, height=0.02,
                                      linewidth=0, facecolor=col)
            leg1.add_patch(rect)
            an = leg1.annotate(lab, xy=(0.3, 1-0.03*i - 0.047), size=6)
            renderer = fig.canvas.get_renderer()
            bb = an.get_window_extent(renderer)
            bbox_data = leg1.transAxes.inverted().transform(bb)
            if bbox_data[1][0] > offset:
                offset = bbox_data[1][0]

        for special in special_cells:
            x = special['np'][1].values
            y = special['np'][2].values
            col = special['col']
            cell_id = special['cell_id']
            main_ax.scatter(x, y, s=marker_size*2, marker='^', c=col,
                            edgecolor='black',
                            linewidth='0.2')
            for i in range(0, len(x)):
                main_ax.annotate(cell_id[i], (x[i]+1, y[i]), size=5)
        if ignored_count:
            log_warning('Ignoring %s cluster(s) (too few cells)' %
                        (ignored_count))
        if self.params['species'] in ['mouse', 'human']:
            # check cell type predictions that mismatch between the two methods
            mismatches = {}
            for i in range(len(self.clusters_targets)):
                ct_method1 = self.res_pred.iloc[i][1]
                #ct_method2 = self.res_pred2.iloc[i][0]
                #mismatches[i] = not (ct_method1 == ct_method2)
            # add number of cells
            offset2 = 0
            for i in range(len(self.clusters_targets)):
                idx = np.array(self.leiden_cl) == i
                e = self.embeddings[idx]
                x = e[1].values
                y = e[2].values
                if e.shape[0] <= ignore_clusters:
                    continue
                lt = leg1.text(offset + 0.1, 1-0.03*i - 0.047, len(x), size=6)
                renderer = fig.canvas.get_renderer()
                bb = lt.get_window_extent(renderer)
                bbox_data = leg1.transAxes.inverted().transform(bb)
                if bbox_data[1][0] > offset2:
                    offset2 = bbox_data[1][0]

            # add marker-based annotation
            offset3 = 0
            for i in range(len(self.clusters_targets)):
                idx = np.array(self.leiden_cl) == i
                e = self.embeddings[idx]
                x = e[1].values
                y = e[2].values
                if e.shape[0] <= ignore_clusters:
                    continue
                pred = self.res_pred.iloc[i]
                ct = pred[1]
                l = {'x': offset2 + 0.1, 'y': 1 -
                     0.03*i - 0.047, 's': ct, 'size': 6}
                # if mismatches[i]:
                #    l['color'] = 'red'
                lt = leg1.text(**l)
                renderer = fig.canvas.get_renderer()
                bb = lt.get_window_extent(renderer)
                bbox_data = leg1.transAxes.inverted().transform(bb)
                if bbox_data[1][0] > offset3:
                    offset3 = bbox_data[1][0]
            # add p-value
            offset4 = 0
            for i in range(len(self.clusters_targets)):
                idx = np.array(self.leiden_cl) == i
                e = self.embeddings[idx]
                x = e[1].values
                y = e[2].values
                if e.shape[0] <= ignore_clusters:
                    continue
                pred = self.res_pred.iloc[i]
                ct = pred[1]
                pval = pred[2]
                if ct == 'Unknown':
                    pval = 'NA'
                else:
                    pval = '{:.1e}'.format(pval)
                lt = leg1.text(offset3 + 0.1, 1-0.03*i - 0.047, pval, size=5)
                renderer = fig.canvas.get_renderer()
                bb = lt.get_window_extent(renderer)
                bbox_data = leg1.transAxes.inverted().transform(bb)
                if bbox_data[1][0] > offset4:
                    offset4 = bbox_data[1][0]
            # add SVM prediction
            # offset5 = 0
            # for i in range(len(self.clusters_targets)):
                # idx = np.array(self.leiden_cl) == i
                # e = self.embeddings[idx]

                # x = e[1].values
                # y = e[2].values

                # if e.shape[0] <= ignore_clusters:
                    # continue

                # item = self.res_pred2.iloc[i]
                # ct = item[0]

                # l = {'x' : offset4 + 0.1, 'y' : 1-0.03*i - 0.047, 's' : ct, 'size' : 6}
                # if mismatches[i]:
                    # l['color'] = 'red'

                # lt = leg1.text(**l)

                # renderer = fig.canvas.get_renderer()
                # bb = lt.get_window_extent(renderer)
                # bbox_data = leg1.transAxes.inverted().transform(bb)

                # if bbox_data[1][0] > offset5:
                    # offset5 = bbox_data[1][0]

            # add probability
            # offset6 = 0
            # y_offset = 0
            # for i in range(len(self.clusters_targets)):
                # idx = np.array(self.leiden_cl) == i
                # e = self.embeddings[idx]

                # x = e[1].values
                # y = e[2].values

                # if e.shape[0] <= ignore_clusters:
                    # continue

                # item = self.res_pred2.iloc[i]
                # ct = item[0]
                # prob = item[1]

                # if ct == 'Unknown':
                    # prob = 'NA'
                # else:
                    # prob = '{:.2f}'.format(prob)

                # lt = leg1.text(offset5 + 0.1, 1-0.03*i - 0.047, prob, size=5)

                # renderer = fig.canvas.get_renderer()
                # bb = lt.get_window_extent(renderer)
                # bbox_data = leg1.transAxes.inverted().transform(bb)

                # if bbox_data[1][0] > offset6:
                    # offset6 = bbox_data[1][0]

                # y_offset = bbox_data[1][1]

            # if dark_bg:
            #    line_col = '#ffffff'
            # else:
            #    line_col = '#000000'

            # leg1.vlines(offset4+0.05, y_offset-0.015, 1-0.03, color=line_col, clip_on=False,
            #            lw=0.5)

            # header
            leg1.text(0.30, 0.99, 'cluster', size=5, rotation=90)
            leg1.text(offset + 0.1, 0.99, 'no. cells', size=5, rotation=90)
            leg1.text(offset2 + 0.1, 0.99,
                      'marker-based\nprediction', size=5, rotation=90)
            leg1.text(offset3 + 0.1, 0.99, 'p-value', size=5, rotation=90)
            #leg1.text(offset4 + 0.1, 0.99, 'SVM-based\nprediction', size=5, rotation=90)
            #leg1.text(offset5 + 0.1, 0.99, 'probability', size=5, rotation=90)
        main_ax.set_ylabel('%s1' % method, size=6)
        main_ax.set_xlabel('%s2' % method, size=6)
        # smaller than default tick label size
        main_ax.tick_params(axis='both', which='major', labelsize=5)
        input_fn = self.params['input_filename']
        main_ax.set_title('%s\n%s' %
                          (title, input_fn.split('/')[-1]), fontsize=7)
        fn = self.get_wd() + \
            OUTPUT['FILENAME_CELL_SCATTER_PLOT_PREFIX'] + method + '.pdf'
        if self.params['timestamp']:
            plt.figtext(0.05, 0, get_time(), size=5)
        plt.savefig(fn, bbox_inches='tight')
        plt.close()
        log_debug('Done generating scatter plot.')

    def genes_exp_per_cluster(self, title=''):
        """ Makes a violin plot of number of expressed genes per
        cluster. """
        log_debug('Entering genes_exp_per_cluster()')
        data_norm = self.data_norm
        cl = self.leiden_cl
        ignore_clusters = self.params['ignore_small_clusters']
        cluster_colors = self.cluster_colors
        data_points = []  # array of arrays
        labels = []
        ticks = []
        idx = 1
        for i, d in data_norm.groupby(by=cl, axis=1):
            if d.shape[1] <= ignore_clusters:
                continue
            genes_expressed = d.apply(lambda x: sum(x > 0), axis=0)
            data_points.append(genes_expressed.values)
            labels.append(i)
            ticks.append(idx)
            idx += 1
        plt.clf()
        fig, ax = plt.subplots(nrows=1, ncols=1, figsize=(7, 5))
        vp = ax.violinplot(data_points, showmeans=False, showmedians=True)
        for i, part in enumerate(vp['bodies']):
            cc = cluster_colors[i]
            part.set_facecolor(cc)
        ax.yaxis.grid(True)
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels)
        ax.set_xlabel('Cluster')
        ax.set_ylabel('Number of expressed genes')
        if self.params['timestamp']:
            plt.figtext(0.05, 0, get_time(), size=5)
        fn = self.get_wd() + OUTPUT['FILENAME_CELL_VIOLIN_GE_PLOT']
        plt.savefig(fn, bbox_inches='tight')
        log_debug('Exiting genes_exp_per_cluster()')

    def cell_scatter_plot_w_gene_overlay(self, title=''):
        """ Makes scatter plot(s) with overlaid gene expression on
        cells. """
        log_debug('Inside cell_scatter_plot_w_gene_overlay()')
        method = self.params['embedding']
        genes = self.params['overlay_genes']
        if genes:
            genes = re.sub(' ', '', genes).upper().split(',')
        else:
            genes = []
        data_norm = self.data_norm
        if self.params['species'] in ['mouse', 'human']:
            symbs = pd.Series(
                data_norm.index.str.extract('(.+)_')[0]).str.upper()
        else:
            symbs = pd.Series(data_norm.index.str.upper())
        cell_count = self.embeddings.shape[0]
        if cell_count > 1000:
            marker_size = 0.8
        else:
            marker_size = 3
        cmap = sb.cubehelix_palette(as_cmap=True)
        for gene in genes:
            row = data_norm.iloc[(symbs == gene).values]
            x = self.embeddings[1].values
            y = self.embeddings[2].values
            plt.clf()
            fig, ax = plt.subplots(nrows=1, ncols=1, figsize=(7, 5))
            #zscore = ((row-row.mean(axis=1)[0])/row.std(axis=1)[0]).values[0]
            points = ax.scatter(x, y, s=marker_size,
                                c=row.values[0], cmap=cmap)
            cb = fig.colorbar(points)
            cb.set_label('%s gene expression (log2 scale)' % (gene))
            fn = self.get_wd() + \
                OUTPUT['FILENAME_CELL_SCATTER_PLOT_PREFIX'] + gene + '.pdf'
            if self.params['timestamp']:
                plt.figtext(0.05, 0, get_time(), size=5)
            plt.savefig(fn, bbox_inches='tight')
        log_debug('Finished cell_scatter_plot_w_gene_overlay()')

    def violin_top(self, title=''):
        """ Makes violin plots of the top expressed genes per
        cluster. """
        log_debug('Entering violin_top()')
        data_norm = self.data_norm
        n = self.params['violin_top']
        cl = self.leiden_cl
        ignore_clusters = self.params['ignore_small_clusters']
        plt.clf()
        fig_size_y = round(len(self.clusters_targets)*2)
        fig, ax = plt.subplots(nrows=self.n_clusters,
                               ncols=1, figsize=(7, fig_size_y))
        fig.subplots_adjust(hspace=1)
        idx = 0
        for cluster_id, d in data_norm.groupby(by=cl, axis=1):
            if d.shape[1] <= ignore_clusters:
                continue
            exp_mean = d.apply(lambda x: np.mean(x), axis=1)
            top = exp_mean.sort_values(ascending=False).head(n).index
            d_filt = d[d.index.isin(top)]
            d_filt = d_filt.reindex(top)
            data_points = []  # array of arrays
            for i, row in d_filt.iterrows():
                data_points.append(row.values)
            vp = ax[idx].violinplot(
                data_points, showmeans=False, showmedians=True)
            ax[idx].grid(axis='y')
            ax[idx].set_xticks(list(range(1, n+1)))
            gene_labels = top
            ax[idx].set_xticklabels(gene_labels, size=5, rotation='vertical')
            ax[idx].set_ylabel('gene expression', size=6)
            ax[idx].set_title('cluster %s' % cluster_id, size=6)
            ax[idx].tick_params(axis='y', which='major', labelsize=6)
            ax[idx].tick_params(axis='y', which='minor', labelsize=6)
            idx += 1
        fn = self.get_wd() + OUTPUT['FILENAME_CELL_VIOLIN_TOP']
        if self.params['timestamp']:
            plt.figtext(0.05, 0, get_time(), size=5)
        plt.savefig(fn, bbox_inches='tight')
        log_debug('Finished violin_top()')
