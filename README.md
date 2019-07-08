```
  ##   #       ####  #    #   ##   
 #  #  #      #    # ##   #  #  #    A pipeline for cell type prediction from
#    # #      #    # # #  # #    #        single cell RNA sequencing data.
###### #      #    # #  # # ###### 
#    # #      #    # #   ## #    #           "Life is better at the beach"
#    # ######  ####  #    # #    #          
```

# Description
`alona` is a Python3-based software pipeline for analysis of single cell RNA sequencing (scRNA-seq) data. `alona` performs normalization, quality control, clustering and cell type annotation of single cell RNA-seq data ([1][1]). In comparison with many established scRNA-seq pipelines, `alona` is not an importable library, but more of a command-line tool integrating current state-of-the-art approaches to scRNA-seq analysis. Running `alona` to analyze scRNA-seq data is simple and fast. The clustering method used is similar to Seurat; i.e., computing a shared nearest neighbor network. `alona` uses the [Leiden algorithm](https://github.com/vtraag/leidenalg) to identify tightly connected communities from the graph.

`alona` also exists as a parallel cloud-based service ([2][2]).

# What it does
![Screenshot](https://panglaodb.se/img/github_screenshot.png)

# Installation
### Requirements
* Linux (alona should work on MacOS too, but it is untested)
* Python >= 3.6

### From GitHub and pip3
```bash
# Clone the repository
git clone https://github.com/oscar-franzen/alona/

# Enter the directory
cd alona

# Install the package
pip3 install .
```

# Usage example
Here is one example of calling the pipeline using the data set [GSM3689776](https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM3689776&format=file&file=GSM3689776%5Fmouse%5F10X%5Fmatrix%2Etxt%2Egz).
```bash
python3 -m alona \
        --species mouse        # specifies our input data is from mouse
        --embedding tSNE       # tSNE is defualt if not specified
        --dark_bg \            # use black background in scatter plots
        --legend \             # plot a legend in scatter plots
        --hvg_n 1000 \         # use 1000 top highly variable genes
        --leiden_res 0.1 \     # clustering parameter
        --output GSM3689776 \  # output directory name
        --header yes \
        --minexpgenes 0.001 \
        --nomito \             # ignore mitochondrial genes in the analysis
        GSM3689776_mouse_10X_matrix.txt.gz
```

# All command line options
```
$[~/bio]> python3 -m alona --help
Usage: alona.py [OPTIONS] FILENAME

Options:
  -o, --output TEXT               Specify name of output directory
  -df, --dataformat [raw|rpkm|log2]
                                  Data format.
                                  (raw read counts, rpkm, log2
                                  normalized data). Default: raw
  -mr, --minreads INTEGER         Minimum number of reads per cell to keep the
                                  cell. Default: 1000
  -mg, --minexpgenes FLOAT        Minimum number of expressed genes as percent
                                  of all cells, i.e. genes expressed in fewer
                                  cells than this are removed. Default: 0.01
  --qc_auto [yes|no]              Automatic filtering of low quality cells.
                                  Default: yes
  --mrnafull                      Data come from a full-length protocol, such
                                  as SMART-seq2.
  -d, --delimiter [auto|tab|space]
                                  Data delimiter. The character used to
                                  separate data values. The default setting is
                                  to autodetect this character. Default: auto
  -h, --header [auto|yes|no]      Data has a header line. The default setting
                                  is to autodetect if a header is present or
                                  not. Default: auto
  -m, --nomito                    Exclude mitochondrial genes from analysis.
  --hvg [seurat|Brennecke2013|scran|Chen2016|M3Drop_smartseq2|M3Drop_UMI]
                                  Method to use for identifying highly
                                  variable genes. Default: seurat
  --hvg_n INTEGER                 Number of top highly variable genes to use.
                                  Default: 1000
  --nn_k INTEGER                  k in the nearest neighbour search. Default:
                                  10
  --prune_snn FLOAT               Threshold for pruning the SNN graph, i.e.
                                  the edges with lower value (Jaccard index)
                                  than this will be removed. Set to 0 to
                                  disable pruning. Increasing this value will
                                  result in fewer edges in the graph. Default:
                                  0.067
  --leiden_partition [RBERVertexPartition|ModularityVertexPartition]
                                  Partitioning algorithm to use. Can be
                                  RBERVertexPartition or
                                  ModularityVertexPartition. Default:
                                  RBERVertexPartition
  --leiden_res FLOAT              Resolution parameter for the Leiden
                                  algorithm (0-1). Default: 0.8
  --ignore_small_clusters INTEGER
                                  Ignore clusters with fewer or equal to N
                                  cells. Default: 10
  --embedding [tSNE|UMAP]         Method used for data projection. Can be
                                  either tSNE or UMAP.
  --perplexity INTEGER            The perplexity parameter in the t-SNE
                                  algorithm. Default: 30
  -s, --species [human|mouse]     Species your data comes from. Default: mouse
  --dark_bg                       Use dark background in scatter plots.
                                  Default: False
  --color_labels                  Plot cell type labels with the same color as
                                  the corresponding cell cluster cells.
                                  Default: True
  --legend                        Use a legend in plots instead of floating
                                  labels inscatter plots for cell types.
                                  Default: False
  -lf, --logfile TEXT             Name of log file. Set to /dev/null if you
                                  want to disable logging to a file. Default:
                                  alona.log
  -ll, --loglevel [regular|debug]
                                  Set how much runtime information is written
                                  to the log file. Default: regular
  -n, --nologo                    Hide the logo. Default: False
  --version                       Display version number.
  --help                          Show this message and exit.
```

# Detailed help for all command line options
option | detailed description
--- | ---
`-out, --output [TEXT]` | Specify name of output directory. If this is not given then a directory with the format: alona_out_N will be created, where N is a 8 letter random string, in the current working directory.
`-df, --dataformat [raw\|rpkm\|log2]` | Specifies how the input data has been normalized. There are currently three options: `raw` means input data are raw read counts (alona will take care of normalization steps); `rpkm` means input data are normalized as RPKM but not logarithmized and alona will not perform any more normalization except for loging; `log2` means that input data have been normalized and logarithmized and alona will not perform these steps. Default: raw
`--mrnafull` | Data come from a full-length protocol, such as SMART-seq2. This option is important if data represent full mRNAs. Drop-seq/10X and similar protocols sequence the *ENDS* of an mRNA, it is therefore not necessary to normalize for gene *LENGTH*. However, if we sequence the complete mRNA then we must also normalize measurements for the length of the gene, since longer genes have more mapped reads. If this option is not set, then cell type prediction may give unexpected results when analyzing full-length mRNA data. Default: False
`--hvg [method]` | Method to use for identifying highly variable genes, must be one of: seurat, Brennecke2013, scran, Chen2016, M3Drop_smartseq2, or M3Drop_UMI. This option specifies the method to be used for identifying variable genes. `seurat` is the method implemented in the Seurat R package ([3][3]). It bins genes according to average expression, then calculates dispersion for each bin as variance to mean ratio. Within each bin, Z-scores are calculated and returned. Z-scores are ranked and the top N are selected. `Brennecke2013` refers to the method proposed by Brennecke et al ([4][4]). `Brennecke2013` estimates and fits technical noise using RNA spikes (technical genes) by fitting a generalized linear model with a gamma function and identity link and the parameterization w=a_1+u+a0. It then uses a chi2 distribution to test the null hypothesis that the squared coefficient of variation does not exceed a certain minimum. FDR<0.10 is considered significant. Currently, `Brennecke2013` uses all the genes to estimate noise. `scran` fits a polynomial regression model to technical noise by modeling the variance versus mean gene expression relationship of ERCC spikes (the original method used local regression) ([5][5]). It then decomposes the variance of the biological gene by subtracting the technical variance component and returning the biological variance component. `Chen2016` ([6][6]) uses linear regression, subsampling, polynomial fitting and gaussian maximum likelihood estimates to derive a set of HVG. `M3Drop_smartseq2` models the dropout rate and mean expression using the Michaelis-Menten equation to identify HVG ([7][7]). `M3Drop_smartseq2` works well with SMART-seq2 data but not UMI data, the former often being sequenced to saturation so zeros are more likely to be dropouts rather than unsaturated sequencing. `M3Drop_UMI` is the corresponding M3Drop method for UMI data. Default: `seurat`
`--hvg_n [int]` | Number of highly variable genes to use. If method is `brennecke` then `--hvg_n` determines how many genes will be used from the genes that are significant. Default: 1000
`--qc_auto [yes\|no]` | Automatically filters low quality cells using five quality metrics and Mahalanobis distances. Three standard deviations from the mean is considered an outlier and will be removed. Default: yes

# Output files

# Contact
* Oscar Franzen <p.oscar.franzen@gmail.com>

# Cite
A manuscript is in preparation.

# License
GPLv3

[1]: https://en.wikipedia.org/wiki/Single-cell_transcriptomics
[2]: http://alona.panglaodb.se/
[3]: https://cran.r-project.org/web/packages/Seurat/index.html
[4]: https://doi.org/10.1038/nmeth.2645
[5]: https://doi.org/10.12688/f1000research.9501.2
[6]: https://doi.org/10.1186/s12864-016-2897-6
[7]: https://doi.org/10.1093/bioinformatics/bty1044
