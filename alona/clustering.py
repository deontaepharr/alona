"""
 This file contains clustering functions used by alona.

 How to use alona:
 https://github.com/oscar-franzen/alona/
 https://alona.panglaodb.se/

 Contact:
 Oscar Franzen <p.oscar.franzen@gmail.com>
"""

import os
import inspect

import ctypes
from ctypes import cdll

import numpy as np
import numpy.ctypeslib as npct

import pandas as pd

import matplotlib.pyplot as plt
import matplotlib.colors as colors
import matplotlib.cm as cmx

import sklearn.manifold

from scipy.sparse import coo_matrix

import leidenalg
import igraph as ig

import alona.irlbpy

from .log import (log_info, log_debug, log_error)
from .constants import OUTPUT

class AlonaClustering():
    """
    Clustering class.
    """

    def __init__(self, alonacell):
        self._alonacell = alonacell
        self.top_hvg = None
        self.pca_components = None
        self.embeddings = None
        self.nn_idx = None
        self.snn_graph = None
        self.leiden_cl = None

    @staticmethod
    def _exp_mean(mat):
        # Axis 0 will act on all the ROWS in each COLUMN
        # Axis 1 will act on all the COLUMNS in each ROW
        return mat.mean(axis=1)

    def find_variable_genes(self):
        """
        Retrieves a list of highly variable genes. Mimics Seurat's `FindVariableGenes`.
        The function bins the genes according to average expression, then calculates
        dispersion for each bin as variance to mean ratio. Within each bin, Z-scores are
        calculated and returned. Z-scores are ranked and the top 1000 are selected.
        """
        log_debug('Finding variable genes')

        # number of bins
        num_bin = 20
        top_genes = 1000

        gene_mean = self._exp_mean(self._alonacell.data_norm)
        # equal width (not size) of bins
        bins = pd.cut(gene_mean, num_bin)

        ret = []

        for _, sliced in self._alonacell.data_norm.groupby(bins):
            # Axis 0 will act on all the ROWS in each COLUMN
            # Axis 1 will act on all the COLUMNS in each ROW
            dispersion = sliced.var(axis=1)/sliced.mean(axis=1)
            zscores = (dispersion-dispersion.mean())/dispersion.std()
            ret.append(zscores)

        ret = pd.concat(ret)
        ret = ret.sort_values(ascending=False)
        self.top_hvg = ret.head(top_genes)

        wd = self._alonacell.alonabase.get_working_dir()
        self.top_hvg.to_csv(wd + OUTPUT['FILENAME_HVG'], header=False)

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

        index_v = self._alonacell.data_norm.index.isin(self.top_hvg.index)
        sliced = self._alonacell.data_norm[index_v]
        lanc = alona.irlbpy.lanczos(sliced, nval=75, maxit=1000)

        # weighing by var (Seurat-style)
        self.pca_components = np.dot(lanc.V, np.diag(lanc.s))

        pd.DataFrame(self.pca_components).to_csv(path_or_buf=out_path, sep=',',
                                                 header=None, index=False)

        log_debug('Finished PCA')

    def tSNE(self, out_path):
        """ Projects data to a two dimensional space using the tSNE algorithm. """
        log_debug('Running t-SNE...')

        tsne = sklearn.manifold.TSNE(n_components=2, n_iter=2000)
        self.embeddings = tsne.fit_transform(pd.DataFrame(self.pca_components))
        pd.DataFrame(self.embeddings).to_csv(path_or_buf=out_path, sep=',',
                                             header=None, index=False)

        log_debug('Finished t-SNE')

    def knn(self, inp_k):
        """
        Nearest Neighbour Search. Finds the k number of near neighbours for each cell.
        """

        log_debug('Performing Nearest Neighbour Search')

        k = inp_k

        libpath = os.path.dirname(inspect.getfile(AlonaClustering)) + '/ANN/annlib.so'
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
        #melted.to_csv(self._alonacell.alonabase.get_working_dir() + \
        #    OUTPUT['FILENAME_SNN_GRAPH'], header=False, index=False)
    
    def snn(self, k, prune_snn):
        """
        Computes Shared Nearest Neighbor (SNN) Graph
        Link weights are number of shared nearest neighbors, so we need to get
        the sum of SNN similarities over all KNNs, which is done with a matrix operation.
        See: http://mlwiki.org/index.php/SNN_Clustering
        """
        
        # TODO: add support for the 'prune_snn' parameter
        # TODO: add flag for prune_thres threshold
        
        snn_path = self._alonacell.alonabase.get_working_dir() + \
            OUTPUT['FILENAME_SNN_GRAPH']
            
        if os.path.exists(snn_path):
            log_debug('Loading SNN from file...')
            self.snn_graph = pd.read_csv(snn_path, header=None)
            return
        
        log_debug('Computing SNN graph...')
        
        k_param = k
        prune_thres = 1/15
        
        # create sparse matrix from tuples
        melted = pd.DataFrame(self.nn_idx).melt(id_vars=[0])[[0,'value']]
        
        rows = np.array(melted[melted.columns[0]])
        cols = np.array(melted[melted.columns[1]])
        d = [1]*len(rows)

        rows = np.array(list(melted[melted.columns[0]].values) + \
            list(range(1,self.nn_idx.shape[0]+1)))
            
        cols = np.array(list(melted[melted.columns[1]]) + \
            list(list(range(1,self.nn_idx.shape[0]+1))))
        
        d = [1]*len(rows)
        
        knn_sparse = coo_matrix((d, (rows-1, cols-1)),
                                 shape=(self.nn_idx.shape[0], self.nn_idx.shape[0]))
        snn_sparse = knn_sparse*knn_sparse.transpose()
        
        # prune using same logic as FindClusters in Seurat
        aa = snn_sparse.nonzero()
        
        node1 = []
        node2 = []
        
        pruned_count = 0
        
        for q1,q2 in zip(aa[0],aa[1]):
            val = snn_sparse[q1,q2]
            strength = val / (k_param + (k_param-val))
            
            snn_sparse[q1,q2] = strength
            
            if strength < prune_thres:
                snn_sparse[q1,q2] = 0
                pruned_count += 1
            else:
                node1.append(q1)
                node2.append(q2)

        log_debug('%s links pruned' % '{:,}'.format(pruned_count))
        
        df = pd.DataFrame({'source_node' : node1, 'target_node' : node2})
        df.to_csv(snn_path, header=None,index=None)
        
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
        
        # construct the graph object
        nn = set(self.snn_graph[0])
        g = ig.Graph()
        g.add_vertices(len(nn))
        g.vs['name'] = list(range(1,len(nn)+1))

        ll = []

        for i in self.snn_graph.itertuples(index=False):
            ll.append(tuple(i))

        g.add_edges(ll)

        cl = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition);
        self.leiden_cl = cl.membership
        
        log_debug('Leiden has finished.')

    def cluster(self):
        """ Cluster cells. """
        k = self._alonacell.alonabase.params['clustering_k']

        self.knn(k)
        self.snn(k, True)
        self.leiden()

    def cell_scatter_plot(self, filename):
        """ Generates a tSNE scatter plot with colored clusters. """
        plt.clf()
        plt.figure(num=None, figsize=(5, 5))
        df = pd.DataFrame(self.embeddings)
        
        uniq = list(set(self.leiden_cl))

        z = range(1, len(uniq))
        hot = plt.get_cmap('hot')
        cNorm  = colors.Normalize(vmin=0, vmax=len(uniq))
        scalarMap = cmx.ScalarMappable(norm=cNorm, cmap=hot)
        
        for i in range(len(uniq)):
            idx = np.array(self.leiden_cl) == i
            e = self.embeddings[idx]
            
            #plt.scatter(df[0], df[1], s=1)
            plt.scatter(e[0], e[1], s=15, color=scalarMap.to_rgba(i), label=uniq[i])

        plt.ylabel('tSNE1')
        plt.xlabel('tSNE2')

        plt.savefig(filename, bbox_inches='tight')
        plt.close()
