---
title: "MOGDx"
bibliography: references.bib
link-citations: true
---

# MOGDx
## Introduction
Multi-omic Graph Diagnosis (MOGDx) is a tool for the integration of omic data and classification of heterogeneous diseases. MOGDx exploits a patient similarity network framework to integrate omic data using Similarity Network Fusion (SNF) [^fn1]. One autoencoder per omic modality is trained and the latent embeddings from each autoencoder are concatenated. These reduced vectors are used as node features in the integrated network. Classification is performed on the fused network using the Graph Convolutional Network (GCN) deep learning algorithm [^fn2]. GCN is a novel paradigm for learning from both network structure and node features. Heterogeneity in diseases confounds clinical trials, treatments, genetic association and more. Accurate stratification of these diseases is therefore critical to optimize treatment strategies for patients with heterogeneous diseases. Previous research has shown that accurate classification of heterogenous diseases has been achieved by integrating and classifying multi-omic data [^fn3,^fn4,^fn5]. MOGDx improves upon this research. The advantages of MOGDx is that it can handle both a variable number of modalities and missing patient data in one or more modalities. Performance of MOGDx was benchmarked on the BRCA TCGA dataset with competitive performance compared to its counterparts. In summary, MOGDx combines patient similarity network integration with graph neural network learning for accurate disease classification. 

## Workflow

## Installation

## Requiremnets

## Contact

## Citations
[^fn1]: Bo Wang et al. “Similarity network fusion for aggregating data types on a genomic scale”. en. In: Nature Methods 11.3 (Mar. 2014). Number: 3 Publisher: Nature Publishing Group, pp. 333–337. ISSN: 1548-7105. DOI: 10.1038/nmeth.2810. URL: https://www.nature.com/articles/nmeth.2810 (visited on 11/07/2022)
[^fn2]: Thomas N. Kipf and Max Welling. Semi-Supervised Classification with Graph Convolutional Networks. en. arXiv:1609.02907 [cs, stat]. Feb. 2017. URL: http : / / arxiv . org / abs / 1609 . 02907 (visited on 09/26/2022).
[^fn3]: Shraddha Pai et al. “netDx: interpretable patient classification using integrated patient similarity networks”. In:
Molecular Systems Biology 15.3 (Mar. 2019). Publisher: John Wiley & Sons, Ltd, e8497. ISSN: 1744-4292. DOI: 10.15252/msb.20188497. URL: https://www.embopress.org/doi/full/10.15252/msb. 20188497 (visited on 12/05/2022).
[^fn4]: Xiao Li et al. “MoGCN: A Multi-Omics Integration Method Based on Graph Convolutional Network for Cancer Subtype Analysis”. eng. In: Frontiers in Genetics 13 (2022), p. 806842. ISSN: 1664-8021. DOI: 10.3389/fgene.2022.806842.
[^fn5]: Tongxin Wang et al. “MOGONET integrates multi-omics data using graph convolutional networks allowing patient classification and biomarker identification”. en. In: Nature Communications 12.1 (June 2021). Number: 1 Publisher: Nature Publishing Group, p. 3445. ISSN: 2041-1723. DOI: 10.1038/s41467-021-23774-w. URL: https://www.nature.com/articles/s41467-021-23774-w (visited on 01/26/2023).