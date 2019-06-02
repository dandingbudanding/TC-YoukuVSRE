import os
import time
import argparse
import torch
import torch.nn as nn
import torch.optim as optim
import torch.backends.cudnn as cudnn
from torch.autograd import Variable
from torch.utils.data import DataLoader

from model.EDVR_arch import EDVR, CharbonnierLoss
from data.youku import YoukuDataset

parser = argparse.ArgumentParser(description='PyTorch Super Res Example')
parser.add_argument('--upscale_factor', type=int, default=4, help="super resolution upscale factor")
parser.add_argument('--batchSize', type=int, default=32, help='training batch size')
parser.add_argument('--start_epoch', type=int, default=1, help='Starting epoch for continuing training')
parser.add_argument('--nEpochs', type=int, default=150, help='number of epochs to train for')
parser.add_argument('--snapshots', type=int, default=5, help='Snapshots')
parser.add_argument('--lr', type=float, default=4e-4, help='Learning Rate. Default=0.0004')
parser.add_argument('--gpu_mode', type=bool, default=True)
parser.add_argument('--threads', type=int, default=0, help='number of threads for data loader to use')
parser.add_argument('--seed', type=int, default=123, help='random seed to use. Default=123')
parser.add_argument('--gpus', default=1, type=int, help='number of gpu')
parser.add_argument('--data_dir', type=str, default='./dataset/train')
# parser.add_argument('--other_dataset', type=bool, default=False, help="use other dataset than vimeo-90k")
parser.add_argument('--nFrames', type=int, default=7)
parser.add_argument('--patch_size', type=int, default=0, help='0 to use original frame size')
parser.add_argument('--data_augmentation', type=bool, default=False)
parser.add_argument('--padding', type=str, default="reflection",
                    help="padding: replicate | reflection | new_info | circle")
parser.add_argument('--model_type', type=str, default='EDVR')
parser.add_argument('--residual', type=bool, default=False)
parser.add_argument('--pretrained_sr', default='weights/3x_edvr_epoch_84.pth', help='sr pretrained base model')
parser.add_argument('--pretrained', type=bool, default=False)
parser.add_argument('--save_folder', default='weights/', help='Location to save checkpoint models')

opt = parser.parse_args()
gpus_list = range(opt.gpus)
cudnn.benchmark = True

cuda = opt.gpu_mode
if cuda and not torch.cuda.is_available():
    raise Exception("No GPU found, please run without --cuda")

torch.manual_seed(opt.seed)
if cuda:
    torch.cuda.manual_seed(opt.seed)

print(opt)


def single_forward(imgs_in, net):
    with torch.no_grad():
        print(imgs_in.shape)
        model_output = net(imgs_in)
        if isinstance(model_output, list) or isinstance(model_output, tuple):
            output = model_output[0]
        else:
            output = model_output
    return output


def train(e):
    epoch_loss = 0
    model.train()
    for iteration, batch in enumerate(train_set, 1):
        lr_seq, gt = batch[0], batch[1]
        if cuda:
            lr_seq = Variable(lr_seq, requires_grad=True).cuda(gpus_list[0])

        optimizer.zero_grad()
        t0 = time.time()
        prediction = single_forward(lr_seq, model)
        prediction_f = Variable(prediction.data.float().cpu().squeeze(0), requires_grad=True)

        img_size=prediction_f.shape
        avgpool=torch.nn.AvgPool2d((2, 2), stride=(2, 2))
        prediction_f_pool = avgpool(prediction_f[(1,2), :, :])
        gt_pool = avgpool(gt[(1,2), :, :])
        loss = criterion(prediction_f[1, :, :], gt[1, :, :])+criterion(prediction_f_pool, gt_pool)
        t1 = time.time()

        epoch_loss += loss.item()
        loss.backward()
        optimizer.step()

        print(f"===> Epoch[{e}]({iteration}/{len(train_set)}):",
              f" Loss: {loss.item():.4f} || Timer: {(t1 - t0):.4f} sec.")

    print(f"===> Epoch {e} Complete: Avg. Loss: {epoch_loss / len(train_set):.4f}")


def checkpoint(epoch_now):
    model_out_path = opt.save_folder + str(
        opt.upscale_factor) + 'x_' + opt.model_type + 'yk' + "_epoch_{}.pth".format(epoch_now)
    torch.save(model.state_dict(), model_out_path)
    print("Checkpoint saved to {}".format(model_out_path))


print('===> Loading dataset')
train_set = YoukuDataset(opt.data_dir, opt.upscale_factor, opt.nFrames,
                         opt.data_augmentation, opt.patch_size, opt.padding)
training_data_loader = DataLoader(dataset=train_set, num_workers=opt.threads,
                                  batch_size=opt.batchSize, shuffle=True)

print('===> Building model ', opt.model_type)
if opt.model_type == 'EDVR':
    model = EDVR(128, opt.nFrames, 8, 5, 40)  # TODO edvr参数
else:
    model = None

if cuda:
    model = torch.nn.DataParallel(model, device_ids=gpus_list)

criterion = CharbonnierLoss()

if opt.pretrained:
    model_name = os.path.join(opt.save_folder + opt.pretrained_sr)
    if os.path.exists(model_name):
        model.load_state_dict(torch.load(model_name, map_location=lambda storage, loc: storage))
        print('Pre-trained SR model is loaded.')

if cuda:
    model = model.cuda(gpus_list[0])
    criterion = criterion.cuda(gpus_list[0])

optimizer = optim.Adam(model.parameters(), lr=opt.lr, betas=(0.9, 0.999), eps=1e-8)

for epoch in range(opt.start_epoch, opt.nEpochs + 1):
    train(epoch)
    # eval()  # todo 需加入在验证集检验，满足要求停机

    # todo learning rate is decayed by a factor of 10 every half of total epochs
    if (epoch + 1) % (opt.nEpochs / 2) == 0:
        for param_group in optimizer.param_groups:
            param_group['lr'] /= 10.0
        print(f"Learning rate decay: lr={optimizer.param_groups[0]['lr']}")

    if (epoch + 1) % opt.snapshots == 0:
        checkpoint(epoch)

"""
需要调节的：
- padding
- nFrames
- lr 的更新
- batch size
- patch size
- data augmentation 何时增强数据
"""