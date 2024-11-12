import argparse
import pandas as pd
import numpy as np
import os
import sys  
sys.path.insert(0, './MAIN/')
from utils import *
from GNN_MME import *
from train import *
import preprocess_functions

import matplotlib.pyplot as plt
from sklearn.model_selection import StratifiedKFold , train_test_split
import networkx as nx
import torch
from datetime import datetime
import joblib
import warnings
import gc
from palettable import wesanderson
warnings.filterwarnings("ignore")

print("Finished Library Import \n")

def main(args): 
    
    if not os.path.exists(args.output) : 
        os.makedirs(args.output, exist_ok=True)
        
    device = torch.device('cpu' if args.no_cuda else 'cuda') # Get GPU device name, else use CPU
    print("Using %s device" % device)
    get_gpu_memory()

    datModalities , meta = data_parsing(args.input , args.mods , args.target , args.index_col , PROCESSED=False)
    meta = meta.loc[sorted(meta.index)]

    if args.no_shuffle : 
        skf = StratifiedKFold(n_splits=args.n_splits , shuffle=False) 
    else :
        skf = StratifiedKFold(n_splits=args.n_splits , shuffle=True) 

    print(skf)
    
    data_to_save = {}
    output_metrics = []
    test_logits = []
    test_labels = []
    for i, (train_index, test_index) in enumerate(skf.split(meta.index, meta)) :

        h = {}
        MME_input_shapes = []

        train_index , val_index = train_test_split(
        train_index, train_size=0.8, test_size=None, stratify=meta.iloc[train_index]
        )

        train_idx = list(meta.iloc[train_index].index)
        val_idx   = list(meta.iloc[val_index].index)
        test_idx  = list(meta.iloc[test_index].index)

        all_graphs = {}
        data_to_save[i] = {}
        for mod in datModalities : 
            count_mtx = datModalities[mod]

            datMeta = meta.loc[count_mtx.index]
            datMeta = datMeta.loc[sorted(datMeta.index)]
            count_mtx = count_mtx.loc[datMeta.index]

            train_index_mod = list(set(train_idx) & set(datMeta.index))
            val_index_mod = list(set(val_idx) & set(datMeta.index))

            if mod in ['mRNA' , 'miRNA'] : 
                pipeline = 'DESeq' 
                if mod == 'mRNA' : 
                    gene_exp = True
                else : 
                    gene_exp = False
            else : 
                pipeline = 'LogReg'

            if pipeline == 'DESeq' :
                print('Performing Differential Gene Expression for Feature Selection')

                count_mtx , datMeta = preprocess_functions.data_preprocess(count_mtx.astype(int).astype(np.float32), 
                                                            datMeta , gene_exp = True)

                train_index_mod = list(set(train_index_mod) & set(count_mtx.index))
                dds, vsd, top_genes = preprocess_functions.DESEQ(count_mtx , datMeta , args.target , 
                                            n_genes=500,train_index=train_index_mod)

                data_to_save[i][f'{mod}_extracted_feats'] = list(set(top_genes))
                datExpr = pd.DataFrame(data=vsd , index=count_mtx.index , columns=count_mtx.columns)

            elif pipeline == 'LogReg' : 
                print('Performing Logistic Regression for Feature Selection')

                n_genes = count_mtx.shape[1]
                datExpr = count_mtx.loc[: , (count_mtx != 0).any(axis=0)] # remove any genes with all 0 expression

                train_index_mod = list(set(train_index_mod) & set(datExpr.index))
                val_index_mod = list(set(val_index_mod) & set(datExpr.index))

                extracted_feats , model  = preprocess_functions.elastic_net(datExpr , datMeta , 
                                                             train_index=train_index_mod , 
                                                             val_index=val_index_mod ,
                                                             l1_ratio = 1 , num_epochs=1000)

                data_to_save[i][f'{mod}_extracted_feats']  = list(set(extracted_feats))
                data_to_save[i][f'{mod}_model']  = {'model' : model}

                del model

            if mod in [] : 
                method = 'bicorr'
            elif mod in [ 'mRNA' , 'RPPA' , 'miRNA' , 'CNV' , 'DNAm'] : 
                method = 'pearson'
            elif mod in [] : 
                method = 'euclidean'

            datExpr = datExpr.loc[datMeta.index]

            if len(data_to_save[i][f'{mod}_extracted_feats']) > 0 : 
                G  = preprocess_functions.knn_graph_generation(datExpr , datMeta , method=method , 
                                                    extracted_feats=data_to_save[i][f'{mod}_extracted_feats'],
                                                    node_size =150 , knn = 15 )
            else : 
                G  = preprocess_functions.knn_graph_generation(datExpr , datMeta , method=method , 
                                                extracted_feats=None, node_size =150 , knn = 15 )

            all_graphs[mod] = G

            h[mod] = datExpr
            MME_input_shapes.append(datExpr.shape[1])

        all_idx = list(set().union(*[list(h[mod].index) for mod in h]))
        train_index = indices_removal_adjust(train_index , meta.reset_index()[args.index_col] , all_idx)
        val_index = indices_removal_adjust(val_index , meta.reset_index()[args.index_col] , all_idx)
        test_index = indices_removal_adjust(test_index , meta.reset_index()[args.index_col] , all_idx)

        train_index = np.concatenate((train_index , val_index))

        gc.collect()
        torch.cuda.empty_cache()
        print('Clearing gpu memory')
        get_gpu_memory()

        if len(all_graphs) > 1 : 
            full_graphs = []

            for mod , graph in all_graphs.items() : 
                full_graph = pd.DataFrame(data = np.zeros((len(all_idx) , len(all_idx))) , index=all_idx , columns=all_idx)
                graph = nx.to_pandas_adjacency(graph)
                full_graph.loc[graph.index , graph.index] = graph.values

                full_graphs.append(full_graph)

            adj_snf = preprocess_functions.SNF(full_graphs)

            node_colour = meta.loc[adj_snf.index].astype('category').cat.set_categories(wesanderson.FantasticFox2_5.hex_colors , rename=True)

            g  = preprocess_functions.plot_knn_network(adj_snf , 15 , meta.loc[adj_snf.index] ,
                                                   node_colours=node_colour , node_size=150)
        else : 
            g = list(all_graphs.values())[0]

        label = F.one_hot(torch.Tensor(list(meta.loc[sorted(all_idx)].astype('category').cat.codes)).to(torch.int64))

        h = reduce(merge_dfs , list(h.values()))
        h = h.loc[sorted(h.index)]

        g = dgl.from_networkx(g , node_attrs=['idx' , 'label'])

        g.ndata['feat'] = torch.Tensor(h.to_numpy())

        g.ndata['label'] = label

        model = GCN_MME(MME_input_shapes , args.latent_dim , args.decoder_dim , args.h_feats, len(meta.unique())).to(device)
        print(model)
        print(g)

        g = g.to(device)
        
        loss_plot = train(g, train_index, device ,  model , label, args.epochs , args.lr , args.patience)
        plt.title(f'Loss for split {i}')
        save_path = args.output + '/loss_plots/'
        os.makedirs(save_path, exist_ok=True)
        plt.savefig(f'{save_path}loss_split_{i}.png' , dpi = 200)
        plt.clf()

        sampler = NeighborSampler(
            [15 for i in range(len(model.gnnlayers))],  # fanout for each layer
            prefetch_node_feats=['feat'],
            prefetch_labels=['label'],
        )
        test_dataloader = DataLoader(
            g,
            torch.Tensor(test_index).to(torch.int64).to(device),
            sampler,
            device=device,
            batch_size=1024,
            shuffle=True,
            drop_last=False,
            num_workers=0,
            use_uva=False,
        )
        
        test_output_metrics = evaluate(model , g, test_dataloader)
        
        print(
        "Fold : {:01d} | Test Accuracy = {:.4f} | F1 = {:.4f} ".format(
        i+1 , test_output_metrics[1] , test_output_metrics[2] )
        )
        
        test_logits.extend(test_output_metrics[-2])
        test_labels.extend(test_output_metrics[-1])
        
        output_metrics.append(test_output_metrics)
        if i == 0 : 
            best_model = model
            best_idx = i
        elif output_metrics[best_idx][1] < test_output_metrics[1] : 
            best_model = model
            best_idx   = i

        get_gpu_memory()
        del model
        gc.collect()
        torch.cuda.empty_cache()
        print('Clearing gpu memory')
        get_gpu_memory()

    test_logits = torch.stack(test_logits)
    test_labels = torch.stack(test_labels)
                        
    accuracy = []
    F1 = []
    output_file = args.output + '/' + "test_metrics.txt"
    with open(output_file , 'w') as f :
        i = 0
        for metric in output_metrics :
            i += 1
            f.write("Fold %i \n" % i)
            f.write(f"acc = %2.3f , avg_prc = %2.3f , avg_recall = %2.3f , avg_f1 = %2.3f" % 
                    (metric[1] , metric[3] , metric[4] , metric[2]))
            f.write('\n')
            accuracy.append(metric[1])
            F1.append(metric[2])
            
        f.write('-------------------------\n')
        f.write("%i Fold Cross Validation Accuracy = %2.2f \u00B1 %2.2f \n" %(args.n_splits , np.mean(accuracy)*100 , np.std(accuracy)*100))
        f.write("%i Fold Cross Validation F1 = %2.2f \u00B1 %2.2f \n" %(args.n_splits , np.mean(F1)*100 , np.std(F1)*100))
        f.write('-------------------------\n')

    print("%i Fold Cross Validation Accuracy = %2.2f \u00B1 %2.2f" %(args.n_splits , np.mean(accuracy)*100 , np.std(accuracy)*100))
    
    # Get the current date
    current_date = datetime.now()

    # Extract month and day as string names
    month = current_date.strftime('%B')[:3]  # Full month name
    day = current_date.day
    
    save_path = args.output + '/Models/'
    os.makedirs(save_path, exist_ok=True)
    torch.save({
        'model_state_dict': best_model.state_dict(),
        # You can add more information to save, such as training history, hyperparameters, etc.
    }, f'{save_path}GCN_MME_model_{month}{day}' )
    
    if args.no_output_plots : 
        cmplt = confusion_matrix(test_logits , test_labels , meta.astype('category').cat.categories)
        plt.title('Test Accuracy = %2.1f %%' % (np.mean(accuracy)*100))
        output_file = args.output + '/' + "confusion_matrix.png"
        plt.savefig(output_file , dpi = 300)
        
        precision_recall_plot , all_predictions_conf = AUROC(test_logits, test_labels , meta)
        output_file = args.output + '/' + "precision_recall.png"
        precision_recall_plot.savefig(output_file , dpi = 300)
        
        node_predictions = []
        node_actual = []
        display_label = meta.astype('category').cat.categories
        for pred , true in zip(all_predictions_conf.argmax(1) , test_labels.cpu().detach().numpy().argmax(1))  : 
            node_predictions.append(display_label[pred])
            node_actual.append(display_label[true])

        pd.DataFrame({'Actual' :node_actual , 'Predicted' : node_predictions}).to_csv(args.output + '/Predictions.csv')

        
def construct_parser():
    # Training settings
    parser = argparse.ArgumentParser(description='MOGDx')
    parser.add_argument('--epochs', type=int, default=100, metavar='N',
                        help='number of epochs to train (default: 10)')
    parser.add_argument('--lr', type=float, default=0.1, metavar='LR',
                        help='learning rate (default: 1.0)')
    parser.add_argument('--patience', type=float, default=100,
                        help='Early Stopping Patience (default: 100 batches of 5 -> equivalent of 100*5 = 500)')
    #parser.add_argument('--gamma', type=float, default=0.7, metavar='M',
    #                    help='Learning rate step gamma (default: 0.7)')
    parser.add_argument('--no-cuda', action='store_true', default=False,
                        help='disables CUDA training')
    #parser.add_argument('--seed', type=int, default=None, metavar='S',
    #                    help='random seed (default: random number)')
    #parser.add_argument('--log-interval', type=int, default=10, metavar='N',
    #                    help='how many batches to wait before logging '
    #                    'training status')
    parser.add_argument('--no-output-plots', action='store_false' , default=True,
                        help='Disables Confusion Matrix and TSNE plots')
    parser.add_argument('--split-val', action='store_false' , default=True,
                        help='Disable validation split on AE and GNN')
    parser.add_argument('--no-shuffle', action='store_true' , default=False,
                        help='Disable shuffling of index for K fold split')
    parser.add_argument('--psn-only', action='store_true' , default=False,
                        help='Dont train on any node features')
    parser.add_argument('--no-psn', action='store_true' , default=False,
                        help='Dont train on PSN (removal of edges)')
    parser.add_argument('--val-split-size', default=0.85 , type=float , help='Validation split of training set in'
                        'each k fold split. Default of 0.85 is 60/10/30 train/val/test with a 10 fold split')
    parser.add_argument('--index-col' , type=str , default='', 
                        help ='Name of column in input data which refers to index.'
                        'Leave blank if none.')
    parser.add_argument('--n-splits' , default=10 , type=int, help='Number of K-Fold'
                        'splits to use')
    parser.add_argument('--decoder-dim' , default=64 , type=int , help ='Integer specifying dim of common '
                        'layer to all modalities')
    #parser.add_argument('--layers' , default=[64 , 64], nargs="+" , type=int , help ='List of integrs'
    #                    'specifying GNN layer sizes')
    #parser.add_argument('--layer-activation', default=['elu' , 'elu'] , nargs="+" , type=str , help='List of activation'
    #                    'functions for each GNN layer')

    parser.add_argument('--h-feats', required=True, nargs="+" ,type=int , help ='Integer specifying hidden dim of GNN'
                    'specifying GNN layer size')
    parser.add_argument('-i', '--input', required=True, help='Path to the '
                        'input data for the model to read')
    parser.add_argument('-o', '--output', required=True, help='Path to the '
                        'directory to write output to')
    parser.add_argument('-mods', required=True, nargs = "+" , type=str, help='List of the '
                        'modalities to include in the analysis e.g. mRNA, DNAm')
    parser.add_argument('-ld' , '--latent-dim', required=True, nargs="+", type=int , help='List of integers '
                        'corresponding to the length of hidden dims of each data modality')
    parser.add_argument('--target' , required = True , help='Column name referring to the'
                        'disease classification label')
    return parser

if __name__ == '__main__':
    parser = construct_parser()
    args = parser.parse_args()
    main(args)