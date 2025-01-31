import os
os.environ["DGLBACKEND"] = "pytorch"
import dgl
import torch
import torch.nn as nn
import torch.nn.functional as F
from dgl.nn import GraphConv , SAGEConv, GATConv
from dgl.dataloading import DataLoader, MultiLayerFullNeighborSampler
import tqdm
import sys
import numpy as np
import pandas as pd
orig_sys_path = sys.path[:]
dirname = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0 , os.path.join(dirname , '../Modules/PNetTorch/MAIN'))
from Pnet import MaskedLinear, PNET 
sys.path.insert(0 , os.path.join(dirname , '../Modules/'))
import layer_conductance
sys.path = orig_sys_path


class Encoder(nn.Module):
    """
    A simple feed-forward neural network used as an encoder with two linear layers separated by dropout and batch normalization.

    Attributes:
        encoder (nn.ModuleList): Contains the linear layers of the encoder.
        norm (nn.ModuleList): Contains the batch normalization layers corresponding to each encoder layer.
        decoder (torch.nn.Sequential): A sequential module containing the decoder part of the model.
        drop (nn.Dropout): Dropout layer to prevent overfitting.

    Args:
        input_dim (int): Dimensionality of the input features.
        latent_dim (int): Dimensionality of the latent space (middle layer's output).
        output_dim (int): Dimensionality of the output features after decoding.
    """
    def __init__(self , input_dim , latent_dim , output_dim):
        super().__init__()
        
        self.encoder = nn.ModuleList()
        self.norm   = nn.ModuleList()
    
        self.encoder.extend([
            nn.Linear(input_dim , 500), 
            nn.Linear(500, latent_dim)
        ])
        
        self.norm.extend([
            nn.BatchNorm1d(500),
            nn.BatchNorm1d(latent_dim)
        ])
        
        self.decoder = torch.nn.Sequential(
            nn.Linear(latent_dim, output_dim),
        )
        
        self.drop = nn.Dropout(0.5) 

    def forward(self, x):
        encoded = x
        for layer in range(2) : 
            encoded = self.encoder[layer](encoded)
            encoded = self.drop(encoded)
            encoded = self.norm[layer](encoded)
            
        decoded = self.decoder(encoded)
        
        return decoded 
    
class GSage_MME(nn.Module):
    """
    A multi-modal GraphSAGE model utilizing encoder modules for initial feature transformation, applying GraphSAGE convolution over the graph structure.

    This model combines several data modalities, each processed by separate encoders, integrates the encoded features, and performs graph-based learning to produce node embeddings or class scores.

    Attributes:
        encoder_dims (nn.ModuleList): List of encoder modules for each modality of data.
        gnnlayers (nn.ModuleList): List of GraphSAGE convolution layers for propagating and transforming node features across the graph.
        batch_norms (nn.ModuleList): Batch normalization applied to the outputs of GNN layers except the last layer.
        num_layers (int): Total number of GNN layers.
        input_dims (list): List of input dimensions, one for each data modality.
        hidden_feats (list): List of the feature dimensions for each hidden layer in the GNN.
        num_classes (int): Number of output classes or the dimension of the output features.
        drop (nn.Dropout): Dropout layer applied after each GNN layer for regularization.

    Args:
        input_dims (list): Input dimensions for each modality of input data.
        latent_dims (list): Latent dimensions for corresponding encoders processing each modality of input data.
        decoder_dim (int): Unified dimension to which all modalities are decoded.
        hidden_feats (list): Dimensions for hidden layers of the GNN.
        num_classes (int): Number of classes for classification tasks.
        PNet (optional): A PNet model for embedding pathway networks, used as an optional modality-specific encoder.
    """
    def __init__(self, input_dims, latent_dims , decoder_dim , hidden_feats , num_classes, PNet=None):
        
        super().__init__()
        
        self.encoder_dims = nn.ModuleList()
        self.gnnlayers = nn.ModuleList()
        self.batch_norms = nn.ModuleList()
        self.num_layers = len(hidden_feats) + 1
        self.input_dims = input_dims
        self.hidden_feats = hidden_feats
        self.num_classes = num_classes

        # GCN with Encoder reduced dim input and pooling scheme
        for modality in range(len(input_dims)):  # excluding the input layer
            if PNet != None : 
                self.encoder_dims.append(PNET(reactome_network=PNet, input_dim=input_dims[modality] , output_dim=decoder_dim, 
                      activation = nn.ReLU , dropout=0.5 , filter_pathways=False , input_layer_mask = None))
            else : 
                self.encoder_dims.append(Encoder(input_dims[modality] , latent_dims[modality] , decoder_dim))
        
        for layers in range(self.num_layers) :
            if layers < self.num_layers -1 :
                if layers == 0 : 
                    self.gnnlayers.append(
                        SAGEConv(decoder_dim , hidden_feats[layers] , 'mean')
                    )
                else :
                    self.gnnlayers.append(
                        SAGEConv(hidden_feats[layers-1], hidden_feats[layers] , 'mean')
                    )
                self.batch_norms.append(nn.BatchNorm1d(hidden_feats[layers]))
            else : 
                self.gnnlayers.append(
                    SAGEConv(hidden_feats[layers-1], num_classes , 'mean')
                )
                
        self.drop = nn.Dropout(0.5)

    def forward(self, g , h):
        """
        Forward pass for GSage_MME embedding computation.

        Args:
            g (dgl.DGLGraph): Input graph.
            h (torch.Tensor): Feature matrix.

        Returns:
            torch.Tensor: Output after passing through the GNN layers.
        """
        
        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed
            
            prev_dim += dim

        h = node_features/(i+1)
        
        for l , (layer , g_layer) in enumerate(zip(self.gnnlayers , g)) : 
            h = layer(g_layer, h)
            if l != len(self.gnnlayers) - 1:
                h = F.relu(h)
                h = self.batch_norms[l](h)
                h = self.drop(h)
            
        return h
    
    def inference(self, g , h , device , batch_size):
        """
        Perform a forward pass using the model for inference, without computing gradients. Usually used after the model has been trained.

        Args:
            g (dgl.DGLGraph): The DGL graph on which inference is performed.
            h (torch.Tensor): Node features for all nodes in the graph.
            device (torch.device): The device tensors will be sent to.
            batch_size (int): The size of batches to use during inference.

        Returns:
            torch.Tensor: The outputs of the inference.
        """

        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed

            prev_dim += dim

        h = node_features/(i+1)

        sampler = MultiLayerFullNeighborSampler(1, prefetch_node_feats=["feat"])
        dataloader = DataLoader(
            g,
            torch.arange(g.num_nodes()).to(g.device),
            sampler,
            device=device,
            batch_size=batch_size,
            shuffle=False,
            drop_last=False,
            num_workers=0,
        )

        buffer_device = torch.device("cuda")
        pin_memory = buffer_device != device
        feat = h
        
        for l, layer in enumerate(self.gnnlayers):
            y = torch.empty(
                g.num_nodes(),
                self.hidden_feats[l] if l != len(self.gnnlayers) - 1 else self.num_classes,
                dtype=feat.dtype,
                device=buffer_device,
                pin_memory=pin_memory,
            )
            feat = feat.to(device)
            for input_nodes, output_nodes, blocks in tqdm.tqdm(dataloader):
                x = feat[input_nodes]
                h = layer(blocks[0], x)  # len(blocks) = 1
                if l != len(self.gnnlayers) - 1:
                    h = F.relu(h)
                    h = self.batch_norms[l](h)
                    h = self.drop(h)
                # by design, our output nodes are contiguous
                y[output_nodes[0] : output_nodes[-1] + 1] = h.to(buffer_device)
            feat = y
        return y
    
    def embedding_extraction(self, g , h , device , batch_size):
        """
        Extract embeddings for the nodes in the graph. This method is typically used to retrieve node embeddings that can then be used for visualization, clustering, or as input for downstream tasks.

        Args:
            g (dgl.DGLGraph): The graph for which embeddings are to be retrieved.
            h (torch.Tensor): Node features tensor.
            device (torch.device): The device to perform computations on.
            batch_size (int): Size of the batches to use during the computation.

        Returns:
            torch.Tensor: Node embeddings extracted by the model.
        """

        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed

            prev_dim += dim

        h = node_features/(i+1)

        sampler = MultiLayerFullNeighborSampler(1, prefetch_node_feats=["feat"])
        dataloader = DataLoader(
            g,
            torch.arange(g.num_nodes()).to(g.device),
            sampler,
            device=device,
            batch_size=batch_size,
            shuffle=False,
            drop_last=False,
            num_workers=0,
        )

        buffer_device = torch.device("cuda")
        pin_memory = buffer_device != device
        feat = h
        
        for l, layer in enumerate(self.gnnlayers[:-1]):
            y = torch.empty(
                g.num_nodes(),
                self.hidden_feats[l] if l != len(self.gnnlayers) - 1 else self.num_classes,
                dtype=feat.dtype,
                device=buffer_device,
                pin_memory=pin_memory,
            )
            feat = feat.to(device)
            for input_nodes, output_nodes, blocks in tqdm.tqdm(dataloader):
                x = feat[input_nodes]
                h = layer(blocks[0], x)  # len(blocks) = 1
                if l != len(self.gnnlayers) - 1:
                    h = F.relu(h)
                    h = self.batch_norms[l](h)
                    h = self.drop(h)
                # by design, our output nodes are contiguous
                y[output_nodes[0] : output_nodes[-1] + 1] = h.to(buffer_device)
            feat = y
        return y
    
class GCN_MME(nn.Module):
        """
    A multi-modal GraphSAGE model utilizing encoder modules for initial feature transformation, applying GraphConv convolution over the graph structure.

    This model combines several data modalities, each processed by separate encoders, integrates the encoded features, and performs graph-based learning to produce node embeddings or class scores.

    Attributes:
        encoder_dims (nn.ModuleList): List of encoder modules for each modality of data.
        gnnlayers (nn.ModuleList): List of GraphConv convolution layers for propagating and transforming node features across the graph.
        batch_norms (nn.ModuleList): Batch normalization applied to the outputs of GNN layers except the last layer.
        num_layers (int): Total number of GNN layers.
        input_dims (list): List of input dimensions, one for each data modality.
        hidden_feats (list): List of the feature dimensions for each hidden layer in the GNN.
        num_classes (int): Number of output classes or the dimension of the output features.
        drop (nn.Dropout): Dropout layer applied after each GNN layer for regularization.

    Args:
        input_dims (list): Input dimensions for each modality of input data.
        latent_dims (list): Latent dimensions for corresponding encoders processing each modality of input data.
        decoder_dim (int): Unified dimension to which all modalities are decoded.
        hidden_feats (list): Dimensions for hidden layers of the GNN.
        num_classes (int): Number of classes for classification tasks.
        PNet (optional): A PNet model for embedding pathway networks, used as an optional modality-specific encoder.
    """
    def __init__(self, input_dims, latent_dims , decoder_dim , hidden_feats , num_classes, PNet=None):
        
        super().__init__()
        
        self.encoder_dims = nn.ModuleList()
        self.gnnlayers = nn.ModuleList()
        self.batch_norms = nn.ModuleList()
        self.num_layers = len(hidden_feats) + 1
        self.input_dims = input_dims
        self.hidden_feats = hidden_feats
        self.num_classes = num_classes

        # GCN with Encoder reduced dim input and pooling scheme
        
        for modality in range(len(input_dims)):  # excluding the input layer
            if PNet != None : 
                self.encoder_dims.append(PNET(reactome_network=PNet, input_dim=input_dims[modality] , output_dim=decoder_dim, 
                      activation = nn.ReLU , dropout=0.5 , filter_pathways=False , input_layer_mask = None))
            else : 
                self.encoder_dims.append(Encoder(input_dims[modality] , latent_dims[modality] , decoder_dim))
        
        for layers in range(self.num_layers) :
            if layers < self.num_layers -1 :
                if layers == 0 : 
                    self.gnnlayers.append(
                        GraphConv(decoder_dim , hidden_feats[layers])
                    )
                else :
                    self.gnnlayers.append(
                        GraphConv(hidden_feats[layers-1], hidden_feats[layers])
                    )
                self.batch_norms.append(nn.BatchNorm1d(hidden_feats[layers]))
            else : 
                self.gnnlayers.append(
                    GraphConv(hidden_feats[layers-1], num_classes)
                )
                
        self.drop = nn.Dropout(0.5)

    def forward(self, h , g):
        """
        Forward pass for GSage_MME embedding computation.

        Args:
            g (dgl.DGLGraph): Input graph.
            h (torch.Tensor): Feature matrix.

        Returns:
            torch.Tensor: Output after passing through the GNN layers.
        """
        
        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed
            
            prev_dim += dim

        h = node_features/(i+1)
        
        for l , (layer , g_layer) in enumerate(zip(self.gnnlayers , g)) :             
            h = layer(g_layer, h)
            if l != len(self.gnnlayers) - 1:
                h = F.relu(h)
                h = self.batch_norms[l](h)
                h = self.drop(h)
            
        return h
    
    def inference(self, g , h , device , batch_size):
        """
        Perform a forward pass using the model for inference, without computing gradients. Usually used after the model has been trained.

        Args:
            g (dgl.DGLGraph): The DGL graph on which inference is performed.
            h (torch.Tensor): Node features for all nodes in the graph.
            device (torch.device): The device tensors will be sent to.
            batch_size (int): The size of batches to use during inference.

        Returns:
            torch.Tensor: The outputs of the inference.
        """

        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed

            prev_dim += dim

        h = node_features/(i+1)

        sampler = MultiLayerFullNeighborSampler(1, prefetch_node_feats=["feat"])
        dataloader = DataLoader(
            g,
            torch.arange(g.num_nodes()).to(g.device),
            sampler,
            device=device,
            batch_size=batch_size,
            shuffle=False,
            drop_last=False,
            num_workers=0,
        )

        buffer_device = torch.device("cuda")
        pin_memory = buffer_device != device
        feat = h
        
        for l, layer in enumerate(self.gnnlayers):
            y = torch.empty(
                g.num_nodes(),
                self.hidden_feats[l] if l != len(self.gnnlayers) - 1 else self.num_classes,
                dtype=feat.dtype,
                device=buffer_device,
                pin_memory=pin_memory,
            )
            feat = feat.to(device)
            for input_nodes, output_nodes, blocks in tqdm.tqdm(dataloader):
                x = feat[input_nodes]
                h = layer(blocks[0], x)  # len(blocks) = 1
                if l != len(self.gnnlayers) - 1:
                    h = F.relu(h)
                    h = self.batch_norms[l](h)
                    h = self.drop(h)
                # by design, our output nodes are contiguous
                y[output_nodes[0] : output_nodes[-1] + 1] = h.to(buffer_device)
            feat = y
        return y
    
    def embedding_extraction(self, g , h , device , batch_size):
        """
        Extract embeddings for the nodes in the graph. This method is typically used to retrieve node embeddings that can then be used for visualization, clustering, or as input for downstream tasks.

        Args:
            g (dgl.DGLGraph): The graph for which embeddings are to be retrieved.
            h (torch.Tensor): Node features tensor.
            device (torch.device): The device to perform computations on.
            batch_size (int): Size of the batches to use during the computation.

        Returns:
            torch.Tensor: Node embeddings extracted by the model.
        """

        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed

            prev_dim += dim

        h = node_features/(i+1)

        sampler = MultiLayerFullNeighborSampler(1, prefetch_node_feats=["feat"])
        dataloader = DataLoader(
            g,
            torch.arange(g.num_nodes()).to(g.device),
            sampler,
            device=device,
            batch_size=batch_size,
            shuffle=False,
            drop_last=False,
            num_workers=0,
        )

        buffer_device = torch.device("cuda")
        pin_memory = buffer_device != device
        feat = h
        
        for l, layer in enumerate(self.gnnlayers[:-1]):
            y = torch.empty(
                g.num_nodes(),
                self.hidden_feats[l] if l != len(self.gnnlayers) - 1 else self.num_classes,
                dtype=feat.dtype,
                device=buffer_device,
                pin_memory=pin_memory,
            )
            feat = feat.to(device)
            for input_nodes, output_nodes, blocks in tqdm.tqdm(dataloader):
                x = feat[input_nodes]
                h = layer(blocks[0], x)  # len(blocks) = 1
                if l != len(self.gnnlayers) - 1:
                    h = F.relu(h)
                    h = self.batch_norms[l](h)
                    h = self.drop(h)
                # by design, our output nodes are contiguous
                y[output_nodes[0] : output_nodes[-1] + 1] = h.to(buffer_device)
            feat = y
        return y

    def feature_importance(self, test_dataloader , device):
        """
        Calculate feature importances using the Conductance algorithm through Captum.

        Args:
            test_dataset (torch.Tensor): The dataset for which to calculate importances.
            target_class (int): The target class index for which to calculate importances.

        Returns:
            pd.DataFrame: A dataframe containing the feature importances.
        """
        print('Feature Level Importance')
        feature_importances = torch.zeros((len(test_dataloader.indices), np.sum(self.input_dims)) , device=device)

        prev_dim = 0
        for i , (pnet , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 
            cond = layer_conductance.LayerConductance(self, pnet.layers[0])

            for it, (input_nodes, output_nodes, blocks) in enumerate(test_dataloader):
                x = blocks[0].srcdata["feat"]
                y = blocks[-1].dstdata["label"].max(dim=1)[1]
                
                n = x.shape[0]
                nan_rows = torch.isnan(x[: , prev_dim:prev_dim + dim]).any(dim=1)
                    
                for target_class in y.unique() : 
                    conductance = cond.attribute(x, target=target_class, additional_forward_args=blocks, internal_batch_size =128 , attribute_to_layer_input=True)
                    
                    imputed_idx = torch.where(nan_rows)[0]
                    reindex = list(range(n))
                    for imp_idx in imputed_idx :
                        reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                        del reindex[-1]
        
                    cond_imputed = torch.concat([conductance , torch.zeros(n , conductance.shape[1], device=device)])[reindex]
                    cond_imputed = cond_imputed[test_dataloader.indices]

                    feature_importances[y == target_class, prev_dim:prev_dim + dim] = cond_imputed[y == target_class]
                    
            prev_dim += dim
                
        data_index = getattr(pnet, 'data_index', np.arange(len(y)))

        feature_importances = pd.DataFrame(feature_importances.detach().cpu().numpy(),
                                           index=data_index,
                                           columns=self.features)
        
        self.feature_importances = feature_importances
        
        return self.feature_importances

    def layerwise_importance(self, test_dataloader , device):
        """
        Compute layer-wise importance scores across all layers for given targets.

        Args:
            test_dataset (torch.Tensor): The dataset for which to calculate importances.
            target_class (int): The target class index for importance calculation.

        Returns:
            List[pd.DataFrame]: A list containing the importance scores for each layer.
        """
        
        layer_importance_scores = {}
        prev_dim = 0
        for i , (pnet , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 
            layer_importance_scores[i] = []
            for lvl , level in enumerate(pnet.layers) :
                print(level)
                cond = layer_conductance.LayerConductance(self, level)

                cond_vals = []
                for it, (input_nodes, output_nodes, blocks) in enumerate(test_dataloader):
                    x = blocks[0].srcdata["feat"]
                    y = blocks[-1].dstdata["label"].max(dim=1)[1]
                    
                    n = x.shape[0]
                    nan_rows = torch.isnan(x[: , prev_dim:prev_dim + dim]).any(dim=1)

                    cond_vals_tmp = torch.zeros((y.shape[0], level.weight.shape[0]) , device=device)
                        
                    for target_class in y.unique() : 
                        conductance = cond.attribute(x, target=target_class, additional_forward_args=blocks, internal_batch_size =128)
                        
                        imputed_idx = torch.where(nan_rows)[0]
                        reindex = list(range(n))
                        for imp_idx in imputed_idx :
                            reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                            del reindex[-1]
            
                        cond_imputed = torch.concat([conductance , torch.zeros(n , conductance.shape[1], device=device)])[reindex]
                        cond_imputed = cond_imputed[test_dataloader.indices]

                        cond_vals_tmp[y == target_class] = cond_imputed[y == target_class]
                    
                    cond_vals.append(cond_vals_tmp)

                cond_vals = torch.cat(cond_vals , dim=0)
                
                cols = pnet.layer_info[lvl]
                data_index = getattr(pnet, 'data_index', np.arange(len(y)))
        
                cond_vals_genomic = pd.DataFrame(cond_vals.detach().cpu().numpy(),
                                                 columns=cols,
                                                 index=data_index)
                layer_importance_scores[i].append(cond_vals_genomic)

            prev_dim += dim
        
        return layer_importance_scores
    
class GAT_MME(nn.Module):
        """
    A multi-modal GAT (Graph Attention Network) model utilizing encoder modules for initial feature transformation, applying GATConv  convolution over the graph structure.

    This model combines several data modalities, each processed by separate encoders, integrates the encoded features, and performs graph-based learning to produce node embeddings or class scores.

    Attributes:
        encoder_dims (nn.ModuleList): List of encoder modules for each modality of data.
        gnnlayers (nn.ModuleList): List of GATConv convolution layers for propagating and transforming node features across the graph.
        batch_norms (nn.ModuleList): Batch normalization applied to the outputs of GNN layers except the last layer.
        num_layers (int): Total number of GNN layers.
        input_dims (list): List of input dimensions, one for each data modality.
        hidden_feats (list): List of the feature dimensions for each hidden layer in the GNN.
        num_classes (int): Number of output classes or the dimension of the output features.
        drop (nn.Dropout): Dropout layer applied after each GNN layer for regularization.

    Args:
        input_dims (list): Input dimensions for each modality of input data.
        latent_dims (list): Latent dimensions for corresponding encoders processing each modality of input data.
        decoder_dim (int): Unified dimension to which all modalities are decoded.
        hidden_feats (list): Dimensions for hidden layers of the GNN.
        num_classes (int): Number of classes for classification tasks.
        PNet (optional): A PNet model for embedding pathway networks, used as an optional modality-specific encoder.
    """
    def __init__(self, input_dims, latent_dims , decoder_dim , hidden_feats, num_classes, PNet=None):
        
        super().__init__()
        
        self.encoder_dims = nn.ModuleList()
        self.gnnlayers    = nn.ModuleList()
        self.batch_norms  = nn.ModuleList()
        self.num_layers   = len(hidden_feats) + 1
        self.input_dims   = input_dims
        self.hidden_feats = hidden_feats
        self.heads        = [3 for _ in range(self.num_layers)] 
        self.num_classes  = num_classes

        # GCN with Encoder reduced dim input and pooling scheme
        for modality in range(len(input_dims)):  # excluding the input layer
            if PNet != None : 
                self.encoder_dims.append(PNET(reactome_network=PNet, input_dim=input_dims[modality] , output_dim=decoder_dim, 
                      activation = nn.ReLU , dropout=0.5 , filter_pathways=False , input_layer_mask = None))
            else : 
                self.encoder_dims.append(Encoder(input_dims[modality] , latent_dims[modality] , decoder_dim))
        
        for layers in range(self.num_layers) :
            if layers < self.num_layers -1 :
                if layers == 0 : 
                    self.gnnlayers.append(
                        GATConv(decoder_dim , hidden_feats[layers], num_heads=self.heads[layers])
                    )
                else :
                    self.gnnlayers.append(
                        GATConv(hidden_feats[layers-1]*self.heads[layers-1], hidden_feats[layers], num_heads=self.heads[layers])
                    )
                self.batch_norms.append(nn.BatchNorm1d(hidden_feats[layers]*self.heads[layers]))
            else : 
                self.gnnlayers.append(
                    GATConv(hidden_feats[layers-1]*self.heads[layers-1], num_classes, num_heads=self.heads[layers])
                )
                
        self.drop = nn.Dropout(0.5)

    def forward(self, g , h):
        """
        Forward pass for GSage_MME embedding computation.

        Args:
            g (dgl.DGLGraph): Input graph.
            h (torch.Tensor): Feature matrix.

        Returns:
            torch.Tensor: Output after passing through the GNN layers.
        """
        
        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed
            
            prev_dim += dim

        h = node_features/(i+1)
        
        for l , (layer , g_layer) in enumerate(zip(self.gnnlayers , g)) : 
            h = layer(g_layer, h)
            if l == len(self.gnnlayers) - 1:  # last layer
                h = h.mean(1)
            else:  # other layer(s)
                h = h.flatten(1)
                h = self.batch_norms[l](h)
            
        return h
    
    def inference(self, g , h , device , batch_size):
        """
        Perform a forward pass using the model for inference, without computing gradients. Usually used after the model has been trained.

        Args:
            g (dgl.DGLGraph): The DGL graph on which inference is performed.
            h (torch.Tensor): Node features for all nodes in the graph.
            device (torch.device): The device tensors will be sent to.
            batch_size (int): The size of batches to use during inference.

        Returns:
            torch.Tensor: The outputs of the inference.
        """

        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed

            prev_dim += dim

        h = node_features/(i+1)

        sampler = MultiLayerFullNeighborSampler(1, prefetch_node_feats=["feat"])
        dataloader = DataLoader(
            g,
            torch.arange(g.num_nodes()).to(g.device),
            sampler,
            device=device,
            batch_size=batch_size,
            shuffle=False,
            drop_last=False,
            num_workers=0,
        )

        buffer_device = torch.device("cuda")
        pin_memory = buffer_device != device
        feat = h
        
        for l, layer in enumerate(self.gnnlayers):
            y = torch.empty(
                g.num_nodes(),
                self.hidden_feats[l]*self.heads[l] if l != len(self.gnnlayers) - 1 else self.num_classes,
                dtype=feat.dtype,
                device=buffer_device,
                pin_memory=pin_memory,
            )
            feat = feat.to(device)
            for input_nodes, output_nodes, blocks in tqdm.tqdm(dataloader):
                x = feat[input_nodes]
                h = layer(blocks[0], x)  # len(blocks) = 1
                if l == len(self.gnnlayers) - 1:  # last layer
                    h = h.mean(1)
                else:  # other layer(s)
                    h = h.flatten(1)
                    h = self.batch_norms[l](h)
                # by design, our output nodes are contiguous
                y[output_nodes[0] : output_nodes[-1] + 1] = h.to(buffer_device)
            feat = y
        return y
    
    def embedding_extraction(self, g , h , device , batch_size):
        """
        Extract embeddings for the nodes in the graph. This method is typically used to retrieve node embeddings that can then be used for visualization, clustering, or as input for downstream tasks.

        Args:
            g (dgl.DGLGraph): The graph for which embeddings are to be retrieved.
            h (torch.Tensor): Node features tensor.
            device (torch.device): The device to perform computations on.
            batch_size (int): Size of the batches to use during the computation.

        Returns:
            torch.Tensor: Node embeddings extracted by the model.
        """

        prev_dim = 0
        node_features = 0

        for i , (Encoder , dim) in enumerate(zip(self.encoder_dims , self.input_dims)) : 

            x = h[: , prev_dim:dim+prev_dim]
            n = x.shape[0]
            nan_rows = torch.isnan(x).any(dim=1)
            decoded = Encoder(x[~nan_rows])

            imputed_idx = torch.where(nan_rows)[0]
            reindex = list(range(n))
            for imp_idx in imputed_idx :
                reindex.insert(imp_idx, reindex[-1])  # Insert the last index at the desired position
                del reindex[-1]

            decoded_imputed = torch.concat([decoded , torch.median(decoded, dim=0).values.repeat(n).reshape(n , decoded.shape[1])])[reindex]

            node_features += decoded_imputed

            prev_dim += dim

        h = node_features/(i+1)

        sampler = MultiLayerFullNeighborSampler(1, prefetch_node_feats=["feat"])
        dataloader = DataLoader(
            g,
            torch.arange(g.num_nodes()).to(g.device),
            sampler,
            device=device,
            batch_size=batch_size,
            shuffle=False,
            drop_last=False,
            num_workers=0,
        )

        buffer_device = torch.device("cuda")
        pin_memory = buffer_device != device
        feat = h
        
        for l, layer in enumerate(self.gnnlayers[:-1]):
            y = torch.empty(
                g.num_nodes(),
                self.hidden_feats[l]*self.heads[l] if l != len(self.gnnlayers) - 2 else self.hidden_feats[l],
                dtype=feat.dtype,
                device=buffer_device,
                pin_memory=pin_memory,
            )
            feat = feat.to(device)
            for input_nodes, output_nodes, blocks in tqdm.tqdm(dataloader):
                x = feat[input_nodes]
                h = layer(blocks[0], x)  # len(blocks) = 1
                if l == len(self.gnnlayers) - 2:  #second last layer
                    h = h.mean(1)
                else:  # other layer(s)
                    h = h.flatten(1)
                    h = self.batch_norms[l](h)
                # by design, our output nodes are contiguous
                y[output_nodes[0] : output_nodes[-1] + 1] = h.to(buffer_device)
            feat = y
        return y