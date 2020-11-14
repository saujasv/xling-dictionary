import json
import torch
import torch.nn as nn
import torch.optim as optim
from torch.optim import lr_scheduler
import numpy as np
import torchvision
from torchvision import datasets, models, transforms
from matplotlib import pyplot as plt
import time
import os
import copy
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms, utils
from helper_functions import *
from transformers import AutoTokenizer
import faiss

class XLingualDataset(Dataset):
    '''
    Reverse dictionary data loader for training
    '''

    def __init__(self, dataset_path, index_path):
        '''
        Init class method

        Arguments:
            dataset_path - path to json data
            index_paths - dict that maps language tag to faiss index path
        '''

        self.lang_map = {'HI': 'hi', 'BE': 'bn', 'GU': 'gu', 'OD': 'or', 'PU': 'pa', 'EN': 'en', 'MA': 'mr'}
        self.lang_map = {lang: lang for lang in self.lang_map}
        self.dataset = read_json_file(dataset_path)
        self.targets = list()
        self.tokenizer = AutoTokenizer.from_pretrained("ai4bharat/indic-bert")
        self.max_seq_length = 128

        for lang in self.lang_map.keys():
            with open(os.path.join(index_path, lang + ".vocab"), 'r') as f:
                word2idx = {line.strip(): i for i, line in enumerate(f)}

            index = faiss.read_index(os.path.join(index_path, lang + ".index"))

            for d in self.dataset:
                if self.lang_map[d["Target_ID"]] == lang:
                    try:
                        self.targets.append(index.reconstruct(word2idx[d["Target_keyword"]]))
                    except KeyError:
                        print(d["Target_keyword"] + " not found")

        self.language_ids = {'HI': 0, 'BE': 1, 'GU': 2, 'OD': 3, 'PU': 4, 'EN': 5, 'MA': 6}

    def __getitem__(self, idx):
        '''
        Get item function pytorch

        Arguments:
            idx - text index
        '''
        tokens = self.tokenizer(self.dataset[idx]["Source_text"], padding="max_length", truncation=True, max_length=self.max_seq_length, return_tensors="pt")
        target = torch.tensor(self.targets[idx])
        label = torch.ones(1)
        return {
                "phrase": tokens,
                "target": target,
                "source_lang": self.lang_map[self.dataset[idx]["Source_ID"]],
                "target_lang": self.lang_map[self.dataset[idx]["Target_ID"]],
                "target_word": self.dataset[idx]["Target_keyword"],
                "label": label
               }

    def __len__(self):

        '''
        Returns length of dataset
        '''

        return len(self.dataset)

def get_train_collate():
    def collate(batch):
        return {
            "phrase": {
                          'input_ids': torch.cat([b["phrase"]['input_ids'] for b in batch]),
                          'attention_mask': torch.cat([b["phrase"]['attention_mask'] for b in batch]),
                          'token_type_ids': torch.cat([b["phrase"]['token_type_ids'] for b in batch]),
                      }, 
            "target": torch.cat([b["target"].unsqueeze(0) for b in batch]),
            "source_lang": [b["source_lang"] for b in batch],
            "target_lang": [b["target_lang"] for b in batch],
            "target_words": [b["target_word"] for b in batch],
            "label": torch.cat([b["label"].unsqueeze(0) for b in batch]),
        }
    
    return collate

def get_eval_collate(index_path, k):
    def collate(batch):
        return {
            "phrase": {
                          'input_ids': torch.cat([b["phrase"]['input_ids'] for b in batch]),
                          'attention_mask': torch.cat([b["phrase"]['attention_mask'] for b in batch]),
                          'token_type_ids': torch.cat([b["phrase"]['token_type_ids'] for b in batch]),
                      }, 
            "target": torch.cat([b["target"].unsqueeze(0) for b in batch]),
            "source_lang": [b["source_lang"] for b in batch],
            "target_lang": [b["target_lang"] for b in batch],
            "target_words": [b["target_word"] for b in batch],
            "label": torch.cat([b["label"].unsqueeze(0) for b in batch]),
            "index_path": index_path,
            "k": k
        }
    
    return collate

class XLingualLoader(Dataset):
    '''
    Reverse dictionary data loader
    '''

    def __init__(self, dataset_path):
        '''
        Init class method

        Arguments:
            dataset_path - path to json data
        '''

        print("Loading dataset......")
        self.dataset_json = read_json_file(dataset_path)
        print("Dataset loaded!")

        print(len(self.dataset_json))


        self.max_seq_length = 128

        # Tokenizer for Indian languages

        self.tokenizer = AutoTokenizer.from_pretrained("ai4bharat/indic-bert", max_seq_length=self.max_seq_length)
        self.language_ids = {'HI': 0, 'BE': 1, 'GU': 2, 'OD': 3, 'PU': 4, 'EN': 5, 'MA': 6}


    def __getitem__(self, idx):
        '''
        Get item function pytorch

        Arguments:
            idx - text index
        '''

        data = self.convert_dict_2_features(self.dataset_json[idx])

        return self.input_to_tensor(data)


    def preprocess_tokens(self, input_tokens):
        '''
        Function to add special tags for bert and padd to max length

        Arguments:
            feature_dictionary - Feature dictionary with input_ids, attention mask and type ids

        Return:
            tokens_dictionary - Padded features
        '''

        out = {}
        tokens = input_tokens

        special_tokens_count = self.tokenizer.num_special_tokens_to_add()
        if len(tokens) > self.max_seq_length - special_tokens_count:
            tokens = tokens[: (self.max_seq_length - special_tokens_count)]

        #  Adding CLS and SEP tokens
        tokens = [self.tokenizer.cls_token] + tokens + [self.tokenizer.sep_token]
        token_type_ids  = [0]*len(tokens)
        attention_mask  = [1]*len(tokens)

        len_before_padd = len(tokens)

        # Adding pad tokens
        tokens = tokens + [self.tokenizer.pad_token] * (self.max_seq_length - len_before_padd)
        token_type_ids += [0] * (self.max_seq_length - len_before_padd)
        attention_mask += [0] * (self.max_seq_length - len_before_padd)


        out["input_ids"]      = self.tokenizer.convert_tokens_to_ids(tokens)
        out["token_type_ids"] = token_type_ids
        out["attention_mask"] = attention_mask

        return out

    def convert_dict_2_features(self, text_dict):
        '''
        Function to convert input dictionary to features for training bert :)

        Arguments:
            text_dict - input dictionary

        Returns:
            data - encoded input features after tokenization
        '''

        data = {}


        src_tokens    = self.tokenizer.tokenize(text_dict["Source_text"])
        target_tokens = self.tokenizer.tokenize(text_dict["Target_keyword"])
        data["src_id"]    = self.language_ids[text_dict["Source_ID"]]
        data["target_id"] = self.language_ids[text_dict["Target_ID"]]

        data["phrase"] = self.preprocess_tokens(src_tokens)
        data["target"] = self.preprocess_tokens(target_tokens)

        return data



    def __len__(self):

        '''
        Returns length of dataset
        '''

        return len(self.dataset_json)


    def input_to_tensor(self, data):
        '''
        Convert inputs to tensor
        '''
        out = {}
        for key in data:
            if isinstance(data[key], dict):
                if (out.get(key) is None):
                    out[key] = {}

                for key_2 in data[key]:
                    out[key][key_2] = torch.tensor(data[key][key_2], dtype=torch.int)
            else:
                out[key] = torch.tensor([data[key]], dtype=torch.int)

        return out



if __name__ == "__main__":
    dataset = XLingualTrainDataset(dataset_path="../data/filtered/validation.json", index_path="../models/index")
    data_loader = DataLoader(dataset, batch_size=128, shuffle=True)

    for batch, data in enumerate(data_loader):
        #print(batch)
        print(data["phrase"]["input_ids"].shape)
        print(data["target"].shape)

