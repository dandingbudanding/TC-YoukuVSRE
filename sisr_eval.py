from __future__ import print_function
import os
import time
from datetime import datetime
import math
import argparse
import logging
import pickle
import yaml

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torch.backends.cudnn as cudnn
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter

from data.youku import SISRDataset
from model.WDSR_B import MODEL
from models.modules.RRDBNet_arch import RRDBNet

# Training settings
parser = argparse.ArgumentParser(description='PyTorch Super Res Example')
parser.add_argument('--yaml_path', type=str, default="./settings.yaml", help='配置文件路径')

args = parser.parse_args()
with open(args.yaml_path, 'r') as yf:
    opt = yaml.load(yf)
cudnn.benchmark = True
cuda = opt['hardware']['cuda']
logger = logging.getLogger('base')

print(opt)

if cuda and not torch.cuda.is_available():
    raise Exception("No GPU found, please run without --cuda")

torch.manual_seed(opt['hardware']['seed'])
if cuda:
    torch.cuda.manual_seed(opt['hardware']['seed'])
device = torch.device("cuda" if cuda else "cpu")

print('===> Loading dataset')
eval_set = SISRDataset(data_dir=opt['eval_dir'], augment=opt['augment'],
                       patch_size=0, v_freq=10)
eval_loader = DataLoader(dataset=eval_set, num_workers=opt['hardware']['threads'],
                         shuffle=True)

print('===> Building model')
model = MODEL(cuda, n_res=opt['WDSR']['n_resblocks'], n_feats=opt['WDSR']['n_feats'],
              res_scale=opt['WDSR']['res_scale'], n_colors=3, mean=opt['mean']).to(device)
models = list()
if opt['channel'] == 3:
    model.load_state_dict(torch.load(opt['pre_train_path'], map_location=lambda storage, loc: storage))
else:
    for c in range(3):
        models.append(MODEL(cuda, n_res=opt['WDSR']['n_resblocks'], n_feats=opt['WDSR']['n_feats'],
                            res_scale=opt['WDSR']['res_scale'], n_colors=1,
                            mean=[opt['mean'][opt['channel']]]).to(device))
        models[c].load_state_dict(torch.load(opt[f'C{c}_path'], map_location=lambda storage, loc: storage))

criterion = nn.L1Loss().to(device)

re_avgpool = torch.nn.AvgPool2d((2, 2), stride=(2, 2))

print('Pre-trained SR model is loaded.')


def get_ch(img: torch.Tensor, channel: int):
    if channel == 0:  # Y通道
        return img.index_select(1, torch.LongTensor([channel])).to(device)
    elif channel < 3 and channel > 0:  # U和V
        return re_avgpool(img.index_select(1, torch.LongTensor([channel]))).to(device)
    elif channel == 3:  # 444
        return img.to(device)


def convert(img: torch.Tensor, norm=False) -> np.ndarray:
    img = img.data.float().cpu().numpy().flatten()
    if norm:
        img *= 255
    return img.round().astype(np.uint8)


def single_forward(lr, gt, net):
    with torch.no_grad():
        hr = net(lr)
        psnr = psnr_tensor(hr, gt)
        loss = criterion(hr, gt)
    return psnr, loss, hr, gt


def eval_func():
    epoch_loss = 0
    avg_psnr = 0
    if opt['channel'] == 3:
        model.eval()
    else:
        for i in range(3):
            models[i].eval()

    for batch_i, batch in enumerate(eval_loader):
        t0 = time.time()
        res = list()

        if opt['channel'] == 3:
            lr, gt = batch[0].to(device), batch[1].to(device)
            with torch.no_grad():
                hr = model(lr)
                _psnr = psnr_tensor(hr, gt)
                _loss = criterion(hr, gt)
        else:
            for i in range(3):
                psnr, loss, hr, gt = single_forward(get_ch(batch[0], i), get_ch(batch[1], i), models[i])
                res.append((psnr, loss, hr, gt))
            hr = np.concatenate((convert(res[0][2]), convert(res[1][2]), convert(res[2][2])))
            gt = np.concatenate((convert(res[0][3]), convert(res[1][3]), convert(res[2][3])))
            _psnr = psnr_numpy(hr, gt)
            _loss = (4 * res[0][1].item() + res[1][1].item() + res[2][1].item()) / 6

        t1 = time.time()
        epoch_loss += _loss
        avg_psnr += _psnr

        if batch_i % 10 == 0:
            print(f"===> eval({batch_i}/{len(eval_loader)}): Y:{res[0][0]:.4f}, {res[1][1].item():.4f},"
                  + f" U:{res[1][0]:.4f}, {res[1][1].item():.4f}, V:{res[2][0]:.4f}, {res[2][1].item():.4f}, .")
            print(f"===> eval({batch_i}/{len(eval_loader)}):  PSNR: {_psnr:.4f}",
                  f" Loss: {_loss:.4f} || Timer: {(t1 - t0):.4f} sec.")

    avg_psnr /= len(eval_loader)
    avg_loss = epoch_loss / len(eval_loader)
    print(f"===> eval Complete: Avg PSNR: {avg_psnr}, Avg. Loss: {avg_loss:.4f}")
    return avg_psnr


def psnr_tensor(img1: torch.Tensor, img2: torch.Tensor):
    # img1 and img2 have range [0, 255]
    diff = img1 - img2
    mse = torch.mean(diff * diff).item()
    if mse == 0:
        return float('inf')
    return 20 * math.log10(255.0 / math.sqrt(mse))


def psnr_numpy(img1: np.ndarray, img2: np.ndarray):
    # img1 and img2 have range [0, 255]
    diff = (img1 - img2).astype(np.float32)
    mse = np.mean(diff * diff)
    if mse == 0:
        return float('inf')
    return 20 * math.log10(255.0 / math.sqrt(mse))


eval_func()

"""
分别验证
"""
