"""
 This file contains clustering methods used by alona.

 In general, it flows like this:

    1. identify highly variable genes (HVG), retrieve N genes
    2. perform PCA on the HVG, retrieve N components
    3. adjust PCAs by weight
    4. compute KNN
    5. compute SNN from KNN, prune SNN graph
    6. identify communities with leiden algo
    7. run t-SNE on the PCAs

 How to use alona:
 https://github.com/oscar-franzen/alona/
 https://alona.panglaodb.se/

 Contact: Oscar Franzen <p.oscar.franzen@gmail.com>
"""

import os
import sys

import ctypes
from ctypes import cdll

import numpy as np
import numpy.ctypeslib as npct

import pandas as pd

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.colors as colors
import matplotlib.cm as cmx

import sklearn.manifold
from statsmodels import robust

from scipy.sparse import coo_matrix

import leidenalg
import igraph as ig

import alona.irlbpy

from .log import (log_info, log_debug, log_error, log_warning)
from .constants import OUTPUT
from .utils import (get_alona_dir, get_random_color, color_distance, generate_new_color, uniqueColors)
from .hvg import AlonaHighlyVariableGenes

class AlonaClustering():
    """
    Clustering class.
    """

    def __init__(self, alonacell, params):
        self._alonacell = alonacell
        self.hvg = None
        self.pca_components = None
        self.embeddings = None # pd.DataFrame
        self.nn_idx = None
        self.snn_graph = None
        self.leiden_cl = None
        self.params = params
        self.cluster_colors = []

    def find_variable_genes(self):
        hvg_finder = AlonaHighlyVariableGenes(hvg_method=self.params['hvg_method'],
                                              hvg_n=self.params['hvg_cutoff'],
                                              data_norm=self._alonacell.data_norm,
                                              data_ERCC=self._alonacell.data_ERCC)
        self.hvg = hvg_finder.find()
        
        wd = self._alonacell.alonabase.get_wd()
        pd.DataFrame(self.hvg).to_csv(wd + OUTPUT['FILENAME_HVG'], header=False, index=False)

    def PCA(self, out_path):
        """
        Calculate principal components using irlba.

        The augmented implicitly restarted Lanczos bidiagonalization algorithm (IRLBA)
        finds a few approximate largest singular values and corresponding singular
        vectors using a method of Baglama and Reichel.

        A fast and memory-efficient way to compute a partial SVD, principal
        components, and some specialized partial eigenvalue decompositions.

        Reference:
        Baglama, James, and Lothar Reichel. “Augmented implicitly restarted Lanczos
        bidiagonalization methods.” SIAM Journal on Scientific Computing 27.1 (2005):
        19-42.

        Some useful notes about the R implementation:
        http://bwlewis.github.io/irlba/
        """

        log_debug('Running PCA...')

        index_v = self._alonacell.data_norm.index.isin(self.hvg)
        sliced = self._alonacell.data_norm[index_v]
        
        lanc = alona.irlbpy.lanczos(sliced, nval=75, maxit=1000)

        # weighing by var (Seurat-style)
        self.pca_components = np.dot(lanc.V, np.diag(lanc.s))
        self.pca_components = pd.DataFrame(self.pca_components, index=sliced.columns)
        self.pca_components.to_csv(path_or_buf=out_path, sep=',', header=None)

        log_debug('Finished PCA')

    def tSNE(self, out_path):
        """
        Projects data to a two dimensional space using the tSNE algorithm.

        van der Maaten, L.J.P.; Hinton, G.E. Visualizing High-Dimensional Data
        Using t-SNE. Journal of Machine Learning Research 9:2579-2605, 2008.
        """
        log_debug('Running t-SNE...')

        tsne = sklearn.manifold.TSNE(n_components=2,
                                     n_iter=2000,
                                     perplexity=self.params['perplexity'])

        self.embeddings = tsne.fit_transform(self.pca_components)
        self.embeddings = pd.DataFrame(self.embeddings, index=self.pca_components.index)
        self.embeddings.to_csv(path_or_buf=out_path, sep=',', header=None)
        log_debug('Finished t-SNE')

    def knn(self, inp_k):
        """
        Nearest Neighbour Search. Finds the k number of near neighbours for each cell.
        """

        log_debug('Performing Nearest Neighbour Search')

        k = inp_k

        libpath = get_alona_dir() + 'ANN/annlib.so'
        lib = cdll.LoadLibrary(libpath)

        pca_rotated = np.rot90(self.pca_components)
        npa = np.ndarray.flatten(pca_rotated)
        npa2 = npa.astype(np.double).ctypes.data_as(ctypes.POINTER(ctypes.c_double))

        lib.get_NN_2Set.restype = None
        lib.get_NN_2Set.argtypes = [ctypes.POINTER(ctypes.c_double),
                                    ctypes.POINTER(ctypes.c_double),
                                    ctypes.POINTER(ctypes.c_int),
                                    ctypes.POINTER(ctypes.c_int),
                                    ctypes.POINTER(ctypes.c_int),
                                    ctypes.POINTER(ctypes.c_int),
                                    ctypes.POINTER(ctypes.c_double),
                                    ctypes.POINTER(ctypes.c_int),
                                    ctypes.POINTER(ctypes.c_int),
                                    npct.ndpointer(dtype=np.double, ndim=1,
                                                   flags='CONTIGUOUS'),
                                    npct.ndpointer(ctypes.c_int),
                                    npct.ndpointer(dtype=np.double, ndim=1,
                                                   flags='CONTIGUOUS')]
        no_cells = self.pca_components.shape[0]
        no_comps = self.pca_components.shape[1]

        out_index = np.zeros(no_cells*k, 'int32')
        out_dists = np.zeros(no_cells*k)

        lib.get_NN_2Set(npa2,
                        npa2,
                        ctypes.c_int(no_comps),
                        ctypes.c_int(no_cells),
                        ctypes.c_int(no_cells),
                        ctypes.c_int(k),    # k
                        ctypes.c_double(0), # EPS
                        ctypes.c_int(1),    # SEARCHTYPE
                        ctypes.c_int(1),    # USEBDTREE
                        np.ndarray(0),      # SQRAD
                        out_index,
                        out_dists)

        out_index_mat = np.reshape(out_index, (no_cells, k))
        out_dists_mat = np.reshape(out_dists, (no_cells, k))

        self.nn_idx = out_index_mat

        log_debug('Finished NNS')

        #pd.DataFrame(self.nn_idx).to_csv('~/Temp/qq.csv', header=None, index=None)
        #melted = pd.DataFrame(out_index_mat).melt(id_vars=[0])[[0,'value']]
        #melted.to_csv(self._alonacell.alonabase.get_wd() + \
        #    OUTPUT['FILENAME_SNN_GRAPH'], header=False, index=False)

    def snn(self, k, prune_snn):
        """
        Computes Shared Nearest Neighbor (SNN) Graph
        Link weights are number of shared nearest neighbors, so we need to get
        the sum of SNN similarities over all KNNs, which is done with a matrix operation.
        See: http://mlwiki.org/index.php/SNN_Clustering
        """

        snn_path = self._alonacell.alonabase.get_wd() + \
            OUTPUT['FILENAME_SNN_GRAPH']

        if os.path.exists(snn_path):
            log_debug('Loading SNN from file...')
            self.snn_graph = pd.read_csv(snn_path, header=None)
            return

        log_debug('Computing SNN graph...')

        k_param = k

        # create sparse matrix from tuples
        melted = pd.DataFrame(self.nn_idx).melt(id_vars=[0])[[0, 'value']]

        rows = np.array(melted[melted.columns[0]])
        cols = np.array(melted[melted.columns[1]])
        d = [1]*len(rows)

        rows = np.array(list(melted[melted.columns[0]].values) + \
            list(range(1, self.nn_idx.shape[0]+1)))

        cols = np.array(list(melted[melted.columns[1]]) + \
            list(list(range(1, self.nn_idx.shape[0]+1))))

        d = [1]*len(rows)

        knn_sparse = coo_matrix((d, (rows-1, cols-1)),
                                shape=(self.nn_idx.shape[0], self.nn_idx.shape[0]))
        snn_sparse = knn_sparse*knn_sparse.transpose()

        # prune using same logic as FindClusters in Seurat
        aa = snn_sparse.nonzero()

        node1 = []
        node2 = []

        pruned_count = 0

        #print(prune_snn)
        #d = sys.stdin.readline()

        for q1, q2 in zip(aa[0], aa[1]):
            val = snn_sparse[q1, q2]
            strength = val / (k_param + (k_param-val))

            snn_sparse[q1, q2] = strength

            if strength < prune_snn:
                snn_sparse[q1, q2] = 0
                pruned_count += 1
            else:
                node1.append(q1)
                node2.append(q2)

        perc_pruned = (pruned_count/len(aa[0]))*100
        log_debug('%.2f%% (n=%s) of links pruned' % (perc_pruned,
                                                     '{:,}'.format(pruned_count)))

        if perc_pruned > 80:
            log_warning('more than 80% of the edges were pruned')

        df = pd.DataFrame({'source_node' : node1, 'target_node' : node2})
        df.to_csv(snn_path, header=None, index=None)

        self.snn_graph = df

        log_debug('Done computing SNN.')

    def leiden(self):
        """
        Cluster the SNN graph using the Leiden algorithm.

        https://github.com/vtraag/leidenalg

        From Louvain to Leiden: guaranteeing well-connected communities
        Traag V, Waltman L, van Eck NJ
        https://arxiv.org/abs/1810.08473
        """

        log_debug('Running leiden clustering...')
        
        res = self.params['leiden_res']

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
                                      resolution_parameter=res)
        self.leiden_cl = cl.membership

        wd = self._alonacell.alonabase.get_wd()
        fn = wd + OUTPUT['FILENAME_CLUSTERS_LEIDEN']

        pd.DataFrame(self.leiden_cl).to_csv(fn, header=False, index=False)

        log_info('leiden formed %s cell clusters' % len(set(cl.membership)))

        if self.params['loglevel'] == 'debug':
            clc = np.bincount(cl.membership)
            ind = np.nonzero(clc)[0]

            log_debug(('cluster', 'cells'))

            for i in zip(ind, clc):
                log_debug(i)

        log_debug('Leiden has finished.')

    def cluster(self):
        """ Cluster cells. """
        k = self.params['nn_k']

        self.knn(k)
        self.snn(k, self.params['prune_snn'])
        self.leiden()

    def cell_scatter_plot(self, filename, cell_type_obj=None):
        """ Generates a tSNE scatter plot with colored clusters. """
        log_debug('Generating scatter plot...')
        dark_bg = self.params['dark_bg']
        color_labels = self.params['color_labels']
        legend = self.params['legend']
        
        ignore_clusters = self.params['ignore_small_clusters']
        added_labels = []

        def is_overlapping(RectB):
            """ Checks for overlap between cell type labels. """
            for RectA in added_labels:
                if (RectA['X1'] < RectB['X2'] and
                        RectA['X2'] > RectB['X1'] and
                        RectA['Y1'] > RectB['Y2'] and
                        RectA['Y2'] < RectB['Y1']):
                    return True
            return False

        if dark_bg:
            # Don't remove this block.
            # For some reason this block is needed for the --dark_bg to work.
            plt.clf()
            fig = plt.figure(num=None, figsize=(5, 5))
            ax = fig.add_subplot(111)
            plt.style.use('dark_background')
            plt.scatter(1, 1, s=1)
            plt.savefig('/tmp/_.pdf', bbox_inches='tight')
            plt.close()

        plt.clf()
        fig = plt.figure(num=None, figsize=(5, 5))
        ax = fig.add_subplot(111)

        if dark_bg:
            log_debug('using dark background (--dark_bg is set)')
            plt.style.use('dark_background')

        uniq = list(set(self.leiden_cl))

        if len(self.cluster_colors) == 0:
            self.cluster_colors = uniqueColors(len(uniq))
            
        cell_count = self.embeddings.shape[0]
        if cell_count > 1000:
            marker_size = 0.8
        else:
            marker_size = 3
        
        legend_items = []

        for i in range(len(uniq)):
            idx = np.array(self.leiden_cl) == i
            e = self.embeddings[idx]
            x = e[0]
            y = e[1]
            
            if e.shape[0] <= ignore_clusters:
                log_warning('Ignoring cluster: %s b/c only %s cell(s)' % (i, len(x)))
                continue
                
            col = self.cluster_colors[i]
            
            plt.scatter(x, y, s=marker_size, color=col, label=uniq[i])
            renderer = fig.canvas.get_renderer()

            if cell_type_obj != None:
                ct = cell_type_obj.res_pred.iloc[i][1]
                # approximate plotting coordinates
                x_text = np.median(x)
                y_text = max(y[y < (np.median(y) + robust.mad(y)*2.7)])
                
                if not legend:
                    if color_labels == True:
                        ann = plt.annotate(ct, (x_text, y_text), size=5,
                                           color=col)
                    else:
                        ann = plt.annotate(ct, (x_text, y_text), size=5)

                    # get the Bbox bounding the text in display units
                    bb = ann.get_window_extent(renderer=renderer)

                    # from display to data coordinates
                    bbox_data = ax.transData.inverted().transform(bb)

                    X1 = bbox_data[0][0] # left coord
                    X2 = bbox_data[1][0] # right coord
                    Y2 = bbox_data[0][1] # bottom coord
                    Y1 = bbox_data[1][1] # top coord

                    d = {'X1' : X1, 'X2' : X2, 'Y2' : Y2, 'Y1' : Y1, 'ct' : ct}
                    offset = 0
                    while is_overlapping(d):
                        Y2 += 1
                        Y1 += 1
                        offset += 1
                        d = {'X1' : X1, 'X2' : X2, 'Y2' : Y2, 'Y1' : Y1, 'ct' : ct}
                        # emergency break
                        if offset > 100:
                            break

                    ann.set_position((x_text, y_text+offset))
                    added_labels.append(d)
                else:
                    legend_items.append(mpatches.Patch(color=col, label='%s (n=%s)' % (ct, len(x))))
                    
        if legend and cell_type_obj:
            plt.legend(handles=legend_items,
                       borderaxespad=0.,
                       loc='upper left',
                       bbox_to_anchor=(1.05, 1))

        plt.ylabel('tSNE1')
        plt.xlabel('tSNE2')
        plt.savefig(filename, bbox_inches='tight')
        plt.close()

        log_debug('Done generating scatter plot.')
