import argparse
import pandas as pd
import os
import sys  
sys.path.insert(0, './MAIN/')
import Network
import AE
import GNN
import networkx as nx
import matplotlib.pyplot as plt
from sklearn.model_selection import StratifiedKFold , train_test_split
from sklearn.preprocessing import MultiLabelBinarizer
import numpy as np
from palettable.wesanderson import Darjeeling2_5
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from matplotlib.lines import Line2D
mlb = MultiLabelBinarizer()

def data_parsing(DATA_PATH , TARGET , INDEX_COL) : 
    TRAIN_DATA_PATH = [i for i in os.listdir(DATA_PATH) if 'expr' in i.lower()]

    datModalities = {}
    for path in TRAIN_DATA_PATH : 
        dattmp = pd.read_csv(path , index_col=0)
        dattmp.name = path.split('.')[1].split('_')[-1]
        datModalities[dattmp.name] = dattmp

    META_DATA_PATH = [i for i in os.listdir(DATA_PATH) if 'meta' in i.lower()]

    meta = pd.Series(dtype=str)
    for path in META_DATA_PATH : 
        meta_tmp = pd.read_csv(path , index_col=0)
        
        if INDEX_COL == '' :
            pass
        else :
            meta_tmp = meta_tmp.set_index(INDEX_COL)
            
        meta = pd.concat([meta , meta_tmp[TARGET]])

    meta = meta[~meta.index.duplicated(keep='first')]

    return datModalities , meta


def main(args): 

    datModalities , meta = data_parsing(args.input , args.target , args.index_col)

    G = Network.network_from_csv('./raw/graph.csv')

    node_subjects = meta.loc[pd.Series(nx.get_node_attributes(G , 'idx'))].reset_index(drop=True)
    node_subjects.name = args.target

    skf = StratifiedKFold(n_splits=10 , shuffle=True) 

    print(skf)

    output_test      = []
    output_model     = []
    output_generator = []

    mlb.fit_transform(meta.values.reshape(-1,1))

    for i, (train_index, test_index) in enumerate(skf.split(meta.index, meta)):
        
        train_index, val_index = train_test_split(
        train_index, train_size=.85, test_size=None, stratify=node_subjects[train_index]
        )
        
        train_subjects = node_subjects[train_index]
        val_subjects   = node_subjects[val_index]
        test_subjects  = node_subjects[test_index]

        node_features = Network.node_feature_augmentation(G , datModalities , args.ld , 300 , train_index , val_index , test_index)
        nx.set_node_attributes(G , pd.Series(node_features.values.tolist() , index= [i[0] for i in G.nodes(data=True)]) , 'node_features')

        test_metrics , model , generator , gcn = GNN.gnn_train_test(G , train_subjects , val_subjects , test_subjects , 300 , mlb)
        
        output_test.append(test_metrics)
        output_model.append(model)
        output_generator.append(generator)
        
    accuracy = []
    output_file = args.output + "test_metrics.txt"
    with open(args.output , 'w') as f :
        for acc in output_test :
            f.write("loss = %2.3f , acc = %2.3f" % (acc[0] , acc[1]))
            f.write('\n')
            accuracy.append(acc[1])

    f.write("10 Fold Cross Validation Accuracy = %2.2f \u00B1 %2.2f" %(np.mean(accuracy)*100 , np.std(accuracy)*100))

    print("10 Fold Cross Validation Accuracy = %2.2f \u00B1 %2.2f" %(np.mean(accuracy)*100 , np.std(accuracy)*100))

    best_model = np.where(accuracy == np.max(accuracy))[0][-1]
    output_file = args.output + "best_model"
    best_model.save(output_file)

    if args.output_plots : 
        cmplt , pred = GNN.gnn_confusion_matrix(output_model[best_model] , output_generator[best_model] , train_subjects , mlb)
        cmplt.plot(  cmap = Darjeeling2_5.mpl_colormap )
        plt.title('Test Accuracy = %2.1f %%' % (np.mean(accuracy)*100))
        output_file = args.output + "confusion_matrix.png"
        plt.savefig(output_file , dpi = 300)

        tsne_plot = GNN.transform_plot(gcn , output_generator[best_model] , node_subjects , TSNE)
        output_file = args.output + "transform.png"
        tsne_plot.savefig(output_file , dpi = 300)

def construct_parser():
    # Training settings
    parser = argparse.ArgumentParser(description='MOGDx')
    #parser.add_argument('--batch-size', type=int, default=64, metavar='N',
    #                    help='input batch size for training (default: 64)')
    #parser.add_argument('--test-batch-size', type=int, default=1000,
    #                    metavar='N', help='input batch size for testing '
    #                    '(default: 1000)')
    parser.add_argument('--epochs', type=int, default=14, metavar='N',
                        help='number of epochs to train (default: 10)')
    parser.add_argument('--lr', type=float, default=1.0, metavar='LR',
                        help='learning rate (default: 1.0)')
    parser.add_argument('--gamma', type=float, default=0.7, metavar='M',
                        help='Learning rate step gamma (default: 0.7)')
    parser.add_argument('--no-cuda', action='store_true', default=False,
                        help='disables CUDA training')
    #parser.add_argument('--seed', type=int, default=None, metavar='S',
    #                    help='random seed (default: random number)')
    #parser.add_argument('--log-interval', type=int, default=10, metavar='N',
    #                    help='how many batches to wait before logging '
    #                    'training status')
    parser.add_argument('--output-plots', action='store_true' , default=True,
                        help='Disables Confusion Matrix and TSNE plots')
    parser.add_argument('--index-col' , type=str , default='', 
                        help ='Name of column in input data which refers to index.'
                        'Leave blank if none.')
    
    parser.add_argument('-i', '--input', required=True, help='Path to the '
                        'input data for the model to read')
    parser.add_argument('-o', '--output', required=True, help='Path to the '
                        'directory to write output to')
    parser.add_argument('-ld' '--latent-dim', required=True, help='List of integers '
                        'corresponding to the length of hidden dims of each data modality')
    parser.add_argument('--target' , required = True , help='Column name referring to the'
                        'disease classification label')
    return parser

if __name__ == '__main__':
    parser = construct_parser()
    args = parser.parse_args()
    main(args)